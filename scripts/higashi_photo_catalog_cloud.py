#!/usr/bin/env python3
"""
higashi_photo_catalog_cloud.py — Cloud version for GitHub Actions

Same logic as ~/ClaudeWorkspace/_Scripts/higashi_photo_catalog.py but reads
credentials from env vars (no .env file).

Env vars injected by GitHub Actions workflow:
  CLAUDE_KEY_4_CONTENT  — Anthropic API key (Claude Haiku for vision)
  SHEETS_TOKEN_PATH     — path to sheets_token.json written from secret

Scans WhatsApp print > [Property folder] > images in the Higashi shared drive.
For each new image: Vision-describe (PT), room type, quality, staging flag,
cover candidate. Logs to "📸 Photo Catalog" tab in Higashi Tracker.
"""

import os, sys, json, base64, io, time
from datetime import date
from pathlib import Path
import urllib.request, urllib.parse

# ── Config ────────────────────────────────────────────────────────────────────
SHEET_ID         = "1qJnILSR_XOgRaPdTHYy1Qx1gnSyzQTj2E04u8kErfYw"  # Higashi Tracker
CATALOG_TAB      = "📸 Photo Catalog"
WHATSAPP_PARENT  = "1o3vb3wl2NUlUmZEscdEkwBeTFrLtxr-8"  # WhatsApp print folder (Higashi drive)
HIGASHI_DRIVE    = "0AN7aea2IZzE0Uk9PVA"
MAX_PER_RUN      = 100

IMAGE_MIMES = {"image/jpeg", "image/png", "image/heic", "image/heif", "image/webp", "image/tiff"}

TOKEN_FILE_PATH = os.environ.get("SHEETS_TOKEN_PATH", "")
if not TOKEN_FILE_PATH or not Path(TOKEN_FILE_PATH).exists():
    print("❌ SHEETS_TOKEN_PATH not set or file not found")
    sys.exit(1)

ANTHROPIC_KEY = os.environ.get("CLAUDE_KEY_4_CONTENT", "")
if not ANTHROPIC_KEY:
    print("❌ CLAUDE_KEY_4_CONTENT not set")
    sys.exit(1)

# ── Google Auth ───────────────────────────────────────────────────────────────
def get_credentials():
    from google.oauth2.credentials import Credentials
    token_data = json.loads(Path(TOKEN_FILE_PATH).read_text())
    data = urllib.parse.urlencode({
        "client_id":     token_data["client_id"],
        "client_secret": token_data["client_secret"],
        "refresh_token": token_data["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    resp = json.loads(urllib.request.urlopen(req).read())
    return Credentials(
        token=resp["access_token"],
        refresh_token=token_data["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=token_data.get("scopes", [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/spreadsheets",
        ]),
    )

def get_sheet_service(creds):
    from googleapiclient.discovery import build
    return build("sheets", "v4", credentials=creds)

def get_drive_service(creds):
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=creds)

# ── Sheet helpers ─────────────────────────────────────────────────────────────
HEADER = [
    "Date Added", "Property", "Filename", "Drive URL",
    "AI Description (PT)", "Room Type", "Quality ⭐",
    "Needs Staging?", "Staging Notes", "Use for Website?",
    "Cover Candidate?", "Date Photo Taken", "Times Used", "File ID",
]

def ensure_catalog_tab(sheets):
    meta = sheets.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if CATALOG_TAB not in tabs:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": CATALOG_TAB}}}]},
        ).execute()
        sheets.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range=f"'{CATALOG_TAB}'!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [HEADER]},
        ).execute()
        print(f"✅ Created '{CATALOG_TAB}' tab with headers")
    else:
        print(f"✅ '{CATALOG_TAB}' tab exists")

def get_cataloged_keys(sheets) -> set:
    """Return set of (property, filename) tuples already cataloged."""
    try:
        result = sheets.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=f"'{CATALOG_TAB}'!B2:C",
        ).execute()
        rows = result.get("values", [])
        return {(r[0], r[1]) for r in rows if len(r) >= 2}
    except Exception:
        return set()

def append_rows(sheets, rows):
    sheets.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=f"'{CATALOG_TAB}'!A:N",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()

# ── Drive scanning ────────────────────────────────────────────────────────────
def list_children(drive, folder_id, mime=None):
    q = f"'{folder_id}' in parents and trashed=false"
    if mime:
        q += f" and mimeType='{mime}'"
    res = drive.files().list(
        q=q,
        corpora="allDrives",
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
        fields="files(id,name,mimeType,createdTime)",
        pageSize=500,
    ).execute()
    return res.get("files", [])

def collect_property_images(drive):
    """WhatsApp print > [Property folder] > images. Returns list of dicts."""
    images = []
    property_folders = list_children(drive, WHATSAPP_PARENT, mime="application/vnd.google-apps.folder")
    for prop in property_folders:
        prop_name = prop["name"].strip()
        kids = list_children(drive, prop["id"])
        imgs = [k for k in kids if k["mimeType"] in IMAGE_MIMES]
        print(f"  📂 {prop_name}: {len(imgs)} images")
        for img in imgs:
            images.append({
                "id": img["id"],
                "name": img["name"],
                "property": prop_name,
                "mime": img["mimeType"],
                "created": img.get("createdTime", ""),
            })
    return images

# ── Vision description ────────────────────────────────────────────────────────
def download_and_prep(drive, file_id, mime):
    """Download image, convert HEIC→JPEG if needed, compress to <4MB."""
    from googleapiclient.http import MediaIoBaseDownload
    from PIL import Image as PILImage

    buf = io.BytesIO()
    req = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    dl = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    raw = buf.getvalue()

    if mime in {"image/heic", "image/heif"}:
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
    img = PILImage.open(io.BytesIO(raw))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((1600, 1600))
    out = io.BytesIO()
    quality = 80
    while quality >= 30:
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality, optimize=True)
        if len(out.getvalue()) <= 4 * 1024 * 1024:
            break
        quality -= 15
    return base64.b64encode(out.getvalue()).decode(), "image/jpeg"

VISION_PROMPT = """Você está catalogando fotos de imóveis de luxo para um site imobiliário (Hig Negócios Imobiliários — Vale do Paraíba, SP).

Analise esta foto e responda no formato EXATO abaixo (em português):

DESCRIPTION: [1-2 frases descrevendo o ambiente, materiais, mobiliário, iluminação. Específico o suficiente para diferenciar de outros cômodos.]
ROOM: [um destes valores exatos: Cozinha | Quarto | Banheiro | Sala | Sala de Jantar | Área Gourmet | Piscina | Fachada | Varanda | Closet | Escritório | Lavanderia | Hall | Garagem | Jardim | Outro]
QUALITY: [1-5, onde 5 = pronto pra hero/capa, 1 = ruim/não usar]
STAGING_NEEDED: [Yes | No] — Yes APENAS se a cama está sem lençol/desfeita, há colchão exposto, falta decoração óbvia, ou imagem clara de "não pronta para venda". Considere que arrumar a cama com lençol/edredom melhora muito a foto.
STAGING_NOTES: [se Yes, descreva exatamente o que precisa: "adicionar lençol branco e travesseiros", "remover entulho do canto", etc. Se No, deixe vazio.]
COVER_CANDIDATE: [Yes | No] — Yes se for foto que funciona como capa de listagem (ângulo amplo, bem iluminada, mostra ambiente principal). Geralmente fachada, sala principal, piscina, ou cozinha gourmet.
"""

def describe_image(file_id, mime, drive):
    import anthropic as anthropic_sdk
    img_b64, media_type = download_and_prep(drive, file_id, mime)

    client = anthropic_sdk.Anthropic(api_key=ANTHROPIC_KEY)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_b64}},
                {"type": "text", "text": VISION_PROMPT},
            ],
        }],
    )
    text = resp.content[0].text
    out = {"description": "", "room": "Outro", "quality": "3",
           "staging_needed": "No", "staging_notes": "", "cover_candidate": "No"}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("DESCRIPTION:"):
            out["description"] = line.replace("DESCRIPTION:", "").strip()
        elif line.startswith("ROOM:"):
            out["room"] = line.replace("ROOM:", "").strip()
        elif line.startswith("QUALITY:"):
            out["quality"] = line.replace("QUALITY:", "").strip()
        elif line.startswith("STAGING_NEEDED:"):
            out["staging_needed"] = line.replace("STAGING_NEEDED:", "").strip()
        elif line.startswith("STAGING_NOTES:"):
            out["staging_notes"] = line.replace("STAGING_NOTES:", "").strip()
        elif line.startswith("COVER_CANDIDATE:"):
            out["cover_candidate"] = line.replace("COVER_CANDIDATE:", "").strip()
    return out

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("\n🏡 Hig Negócios Imobiliários — Photo Catalog (cloud)")
    print(f"📅 {date.today()} | Cap: {MAX_PER_RUN}\n")

    creds = get_credentials()
    drive = get_drive_service(creds)
    sheets = get_sheet_service(creds)

    ensure_catalog_tab(sheets)
    done_keys = get_cataloged_keys(sheets)
    print(f"📊 Already cataloged: {len(done_keys)} (property, filename) pairs\n")

    print("📁 Scanning WhatsApp print folder...")
    all_images = collect_property_images(drive)
    new_images = [img for img in all_images if (img["property"], img["name"]) not in done_keys]
    print(f"\n📊 Total images: {len(all_images)} | New: {len(new_images)}\n")

    new_rows = []
    processed = 0
    for img in new_images:
        if processed >= MAX_PER_RUN:
            break
        print(f"   🖼️  [{img['property']}] {img['name']} ...", end=" ", flush=True)
        try:
            r = describe_image(img["id"], img["mime"], drive)
            drive_url = f"https://drive.google.com/file/d/{img['id']}/view"
            date_taken = img["created"][:10] if img["created"] else ""
            row = [
                date.today().isoformat(),
                img["property"],
                img["name"],
                drive_url,
                r["description"],
                r["room"],
                r["quality"],
                r["staging_needed"],
                r["staging_notes"],
                "",
                r["cover_candidate"],
                date_taken,
                "0",
                img["id"],
            ]
            new_rows.append(row)
            processed += 1
            print(f"✅ {r['room']} Q{r['quality']} stage={r['staging_needed']}")
            time.sleep(0.3)
        except Exception as e:
            print(f"❌ {str(e)[:80]}")
            continue

    if new_rows:
        append_rows(sheets, new_rows)
        print(f"\n✅ Added {len(new_rows)} rows to '{CATALOG_TAB}'")
    else:
        print("\n✅ No new images to catalog")

    print(f"\n📊 Processed this run: {processed}")

if __name__ == "__main__":
    main()
