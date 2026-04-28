#!/usr/bin/env python3
"""
fix_existing_images.py — Retroactive image quality repair for carousel Drive folders.

Scans version folders in a Drive carousel folder, finds AI-generated images via
media_provenance.json, regenerates prompts via prompt_builder, re-fetches with
image_providers (real-photo tiers first, then AI cascade), re-uploads to Drive.

Flow per slot:
  1. Read media_provenance.json → find source_type == "ai" slots
  2. Read cover.html (or slide HTML) → extract slide text for that slot
  3. Call prompt_builder.build_image_prompt() → fresh specific prompt
  4. Call image_providers.fetch_image() → real photo first, then AI cascade
  5. Upload fixed image to Drive, update media_provenance.json

Usage:
  python fix_existing_images.py --dry-run
  python fix_existing_images.py
  python fix_existing_images.py --provider seedream-4.5
  python fix_existing_images.py --folder 16P2JN74JAAW3HKnmNqPGPrAq7N5jDNii
  python fix_existing_images.py --folder 1gDOjtW_X-_jWtu94pffbDaUsw6VGCKuA --niche brazil

Defaults:
  --folder  16P2JN74JAAW3HKnmNqPGPrAq7N5jDNii  (OPC carousel parent folder)
  --niche   opc
  --provider (none = full cascade)
"""
import argparse, json, os, re, sys, tempfile, time
from pathlib import Path

# Ensure scripts/content_creator is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from image_providers import (
    fetch_real_photo, generate_ai_image, make_filename,
    PROVIDER_NB2, DEFAULT_AI_CASCADE,
)
from prompt_builder import build_image_prompt

# ── Credentials ───────────────────────────────────────────────────────────────
CREDENTIALS = os.environ.get(
    "SHEETS_TOKEN_PATH",
    "/Users/priscilahigashi/ClaudeWorkspace/Credentials/sheets_token.json",
)

# ── Drive helpers ─────────────────────────────────────────────────────────────

def _drive():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(CREDENTIALS)
    return build("drive", "v3", credentials=creds)


def _list_folders(drive, parent_id: str) -> list:
    """List immediate subfolder children of parent_id."""
    q = (f"'{parent_id}' in parents and "
         f"mimeType='application/vnd.google-apps.folder' and trashed=false")
    res = drive.files().list(
        q=q, fields="files(id,name)",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
        pageSize=200,
    ).execute()
    return res.get("files", [])


def _find_file(drive, folder_id: str, name: str) -> dict | None:
    """Return first file matching name in folder_id, or None."""
    q = f"'{folder_id}' in parents and name='{name}' and trashed=false"
    res = drive.files().list(
        q=q, fields="files(id,name)",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute()
    files = res.get("files", [])
    return files[0] if files else None


def _download_bytes(drive, file_id: str) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload
    import io
    req = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue()


def _upload_file(drive, local_path: Path, parent_id: str, filename: str) -> str:
    """Upload or replace a file in parent_id. Returns file ID."""
    from googleapiclient.http import MediaFileUpload
    existing = _find_file(drive, parent_id, filename)
    media = MediaFileUpload(str(local_path), resumable=False)
    if existing:
        drive.files().update(
            fileId=existing["id"], media_body=media, supportsAllDrives=True,
        ).execute()
        return existing["id"]
    result = drive.files().create(
        body={"name": filename, "parents": [parent_id]},
        media_body=media, supportsAllDrives=True, fields="id",
    ).execute()
    return result["id"]


def _find_or_create_folder(drive, parent_id: str, name: str) -> str:
    """Find or create a subfolder named `name` inside parent_id. Returns folder ID."""
    existing = _find_file(drive, parent_id, name)
    if existing:
        return existing["id"]
    res = drive.files().create(
        body={"name": name, "parents": [parent_id],
              "mimeType": "application/vnd.google-apps.folder"},
        supportsAllDrives=True, fields="id",
    ).execute()
    return res["id"]


# ── HTML text extraction ──────────────────────────────────────────────────────

def _extract_slide_texts(html: str) -> dict:
    """Return {slide_num: plain_text} by parsing .slide divs in the carousel HTML."""
    blocks = re.findall(
        r'<(?:div|section)[^>]*class="[^"]*slide[^"]*"[^>]*>(.*?)</(?:div|section)>',
        html, re.DOTALL | re.IGNORECASE,
    )
    if not blocks:
        # Fallback: numbered sections
        blocks = re.findall(r'<section[^>]*>(.*?)</section>', html, re.DOTALL | re.IGNORECASE)
    result = {}
    for i, block in enumerate(blocks, start=1):
        text = re.sub(r"<[^>]+>", " ", block)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 10:
            result[i] = text
    return result


# ── Core repair logic ─────────────────────────────────────────────────────────

def fix_version_folder(
    drive,
    version_folder: dict,
    niche: str,
    dry_run: bool,
    provider: str | None,
    work_dir: Path,
) -> dict:
    """Repair AI images in one version folder. Returns summary dict."""
    folder_id = version_folder["id"]
    folder_name = version_folder["name"]
    summary = {"folder": folder_name, "fixed": 0, "skipped": 0, "errors": 0, "details": []}

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Processing: {folder_name}")

    # Find resources/ subfolder
    resources_folder = _find_file(drive, folder_id, "resources")
    if not resources_folder:
        print(f"  No resources/ folder — skipping")
        summary["skipped"] += 1
        return summary

    resources_id = resources_folder["id"]

    # Read media_provenance.json
    prov_file = _find_file(drive, resources_id, "media_provenance.json")
    if not prov_file:
        print(f"  No media_provenance.json — skipping")
        summary["skipped"] += 1
        return summary

    try:
        prov = json.loads(_download_bytes(drive, prov_file["id"]).decode())
    except Exception as e:
        print(f"  Failed to read provenance: {e}")
        summary["errors"] += 1
        return summary

    # Find images/ subfolder
    images_folder = _find_file(drive, resources_id, "images")
    images_id = images_folder["id"] if images_folder else None

    # Read HTML to extract slide texts for prompt regeneration
    slide_texts = {}
    html_file = _find_file(drive, folder_id, "cover.html")
    if html_file:
        try:
            html = _download_bytes(drive, html_file["id"]).decode("utf-8", errors="ignore")
            slide_texts = _extract_slide_texts(html)
        except Exception as e:
            print(f"  Could not read cover.html: {e}")

    # Collect AI-sourced slots
    ai_slots = []

    cover = prov.get("cover", {})
    if isinstance(cover, dict) and cover.get("source_type") == "ai":
        subject_type = cover.get("subject_type", "place")
        if subject_type != "person":  # never re-generate named-person covers via AI
            ai_slots.append({
                "slot": "cover",
                "slide_num": 1,
                "query": cover.get("query", ""),
                "prompt": cover.get("prompt", ""),
                "subject_type": subject_type,
            })

    for slide_key, slide_data in prov.get("slides", {}).items():
        if isinstance(slide_data, dict) and slide_data.get("source_type") == "ai":
            ai_slots.append({
                "slot": f"slide_{slide_key}",
                "slide_num": int(slide_key),
                "query": slide_data.get("query", ""),
                "prompt": slide_data.get("prompt", ""),
                "subject_type": slide_data.get("subject_type", "place"),
            })

    if not ai_slots:
        print(f"  No AI-sourced slots — nothing to fix")
        summary["skipped"] += 1
        return summary

    print(f"  Found {len(ai_slots)} AI-sourced slot(s)")

    # Create local work dir for this version folder
    local_dir = work_dir / folder_name
    local_dir.mkdir(parents=True, exist_ok=True)

    for slot in ai_slots:
        slide_num = slot["slide_num"]
        query = slot["query"]
        subject_type = slot["subject_type"]
        slot_label = slot["slot"]

        print(f"  → {slot_label}: query='{query[:60]}'")

        if dry_run:
            summary["details"].append({"slot": slot_label, "action": "would_fix"})
            summary["fixed"] += 1
            continue

        # Step 1: Regenerate prompt from slide text (not the old stored prompt)
        slide_text = slide_texts.get(slide_num, query)
        fresh_prompt = build_image_prompt(
            slide_text=slide_text,
            context_image_query=query,
            niche=niche,
            slide_num=slide_num,
            subject_type=subject_type,
            work_dir=str(local_dir),
            save=True,
        )
        if not fresh_prompt:
            print(f"    Skipped (named person or no prompt generated)")
            summary["skipped"] += 1
            continue

        # Step 2: Real-photo tiers first
        prov_slug = provider or PROVIDER_NB2
        filename = make_filename(query or fresh_prompt[:40], prov_slug, slide_num)
        img_path, used_provider = fetch_real_photo(query, str(local_dir), filename)
        source_type = "cc" if used_provider == "wikimedia" else ("stock" if used_provider else "")

        # Step 3: AI cascade if real photos miss
        if not img_path and subject_type != "person":
            img_path, used_provider = generate_ai_image(
                fresh_prompt, str(local_dir), filename, provider
            )
            source_type = "ai" if img_path else ""

        if not img_path:
            print(f"    All tiers failed for {slot_label}")
            summary["errors"] += 1
            summary["details"].append({"slot": slot_label, "action": "failed"})
            continue

        # Step 4: Upload to Drive images/ folder
        local_img = local_dir / "resources" / "images" / filename
        if not images_id:
            images_id = _find_or_create_folder(drive, resources_id, "images")
        _upload_file(drive, local_img, images_id, filename)
        print(f"    ✅ Fixed via {used_provider} → {filename}")

        # Step 5: Update provenance in memory
        rel_path = f"resources/images/{filename}"
        new_entry = {
            "path": rel_path,
            "provider": used_provider,
            "source_type": source_type,
            "query": query,
            "prompt": fresh_prompt,
        }
        if slot_label == "cover":
            prov["cover"].update(new_entry)
        else:
            prov.setdefault("slides", {})[str(slide_num)] = new_entry

        summary["fixed"] += 1
        summary["details"].append({
            "slot": slot_label, "action": "fixed",
            "provider": used_provider, "filename": filename,
        })

    # Write updated provenance back to Drive
    if not dry_run and summary["fixed"] > 0:
        try:
            prov_path = local_dir / "media_provenance.json"
            prov_path.write_text(json.dumps(prov, indent=2), encoding="utf-8")
            _upload_file(drive, prov_path, resources_id, "media_provenance.json")
            print(f"  Provenance updated.")
        except Exception as e:
            print(f"  Warning: failed to update provenance: {e}")

    return summary


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fix AI-generated images in carousel Drive folders."
    )
    parser.add_argument(
        "--folder", default="16P2JN74JAAW3HKnmNqPGPrAq7N5jDNii",
        help="Drive folder ID to scan (default: OPC carousel parent)",
    )
    parser.add_argument(
        "--niche", default="opc",
        choices=["opc", "brazil", "usa", "higashi"],
        help="Content niche (affects prompt style)",
    )
    parser.add_argument(
        "--provider", default=None,
        choices=DEFAULT_AI_CASCADE,
        help="Pin to one AI provider instead of cascading (default: cascade)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be fixed without making any changes",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max version folders to process (0 = all)",
    )
    args = parser.parse_args()

    print(f"fix_existing_images.py")
    print(f"  Folder  : {args.folder}")
    print(f"  Niche   : {args.niche}")
    print(f"  Provider: {args.provider or 'cascade (' + ' → '.join(DEFAULT_AI_CASCADE) + ')'}")
    print(f"  Dry run : {args.dry_run}")

    drive = _drive()

    # List version folders one level down
    top_folders = _list_folders(drive, args.folder)
    version_folders = [f for f in top_folders if re.match(r"v\d+_", f["name"])]

    # Also scan one level deeper (series subfolders → version folders inside)
    for tf in top_folders:
        if not re.match(r"v\d+_", tf["name"]):
            sub = _list_folders(drive, tf["id"])
            version_folders.extend([f for f in sub if re.match(r"v\d+_", f["name"])])

    version_folders.sort(key=lambda f: f["name"])
    print(f"\nFound {len(version_folders)} version folder(s) to inspect.")

    if args.limit:
        version_folders = version_folders[:args.limit]
        print(f"  Processing first {args.limit} only (--limit flag)")

    totals = {"fixed": 0, "skipped": 0, "errors": 0}

    with tempfile.TemporaryDirectory(prefix="fix_images_") as tmp:
        work_dir = Path(tmp)
        for vf in version_folders:
            summary = fix_version_folder(
                drive, vf,
                niche=args.niche,
                dry_run=args.dry_run,
                provider=args.provider,
                work_dir=work_dir,
            )
            totals["fixed"] += summary["fixed"]
            totals["skipped"] += summary["skipped"]
            totals["errors"] += summary["errors"]

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Done.")
    print(f"  Fixed  : {totals['fixed']}")
    print(f"  Skipped: {totals['skipped']}")
    print(f"  Errors : {totals['errors']}")


if __name__ == "__main__":
    main()
