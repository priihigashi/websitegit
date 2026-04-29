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
import argparse, hashlib, json, os, re, sys, tempfile, time
from pathlib import Path
from typing import Optional

# Ensure scripts/content_creator is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from image_providers import (
    fetch_real_photo, generate_ai_image, make_filename,
    PROVIDER_NB2, DEFAULT_AI_CASCADE,
)
from prompt_builder import build_image_prompt, build_stock_query as _build_stock_query, extract_slide_texts as _extract_slide_texts

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


def _find_file(drive, folder_id: str, name: str) -> Optional[dict]:
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


def _list_files(drive, folder_id: str) -> list:
    """List all non-folder files in folder_id."""
    q = (f"'{folder_id}' in parents and "
         f"mimeType!='application/vnd.google-apps.folder' and trashed=false")
    res = drive.files().list(
        q=q, fields="files(id,name)",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
        pageSize=100,
    ).execute()
    return res.get("files", [])


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


def _move_to_replaced(drive, images_folder_id: str, filename: str) -> None:
    """Move an old image file to images/replaced/ subfolder. Non-fatal on failure."""
    try:
        q = (f"'{images_folder_id}' in parents and name='{filename}' "
             f"and mimeType!='application/vnd.google-apps.folder' and trashed=false")
        res = drive.files().list(
            q=q, fields="files(id,name)",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        files = res.get("files", [])
        if not files:
            return
        file_id = files[0]["id"]
        replaced_id = _find_or_create_folder(drive, images_folder_id, "replaced")
        drive.files().update(
            fileId=file_id,
            addParents=replaced_id,
            removeParents=images_folder_id,
            supportsAllDrives=True,
            fields="id,parents",
        ).execute()
        print(f"    Moved old image to replaced/ → {filename}")
    except Exception as e:
        print(f"    Could not move old image to replaced/ (non-fatal): {e}")


# ── Core repair logic ─────────────────────────────────────────────────────────

def fix_version_folder(
    drive,
    version_folder: dict,
    niche: str,
    dry_run: bool,
    provider: Optional[str],
    work_dir: Path,
    skip_provider_per_slot: Optional[dict] = None,
    backup_pngs: bool = False,
) -> dict:
    """Repair AI images in one version folder. Returns summary dict.

    Optional params (auto_fixer use case):
        skip_provider_per_slot: {slot_label: provider_to_skip} — when reviewer's
            auto-fix retries a slot, skip the AI provider already used so the
            cascade tries a different one.
        backup_pngs: when True, copy png/ children to png_pre_fix_<ts>/ before
            any image change so rendered carousels remain recoverable.
    """
    skip_provider_per_slot = skip_provider_per_slot or {}
    folder_id = version_folder["id"]
    folder_name = version_folder["name"]
    summary = {"folder": folder_name, "fixed": 0, "skipped": 0, "errors": 0, "details": []}
    seen_hashes: set = set()  # MD5 hashes of images already accepted in this folder

    # PNG backup (Goal 2 hook — runs BEFORE any image work so we can always roll back)
    if backup_pngs and not dry_run:
        try:
            png_folder = _find_file(drive, folder_id, "png")
            if png_folder:
                ts = time.strftime("%Y%m%d_%H%M%S")
                backup_id = _find_or_create_folder(drive, folder_id, f"png_pre_fix_{ts}")
                for f in _list_files(drive, png_folder["id"]):
                    drive.files().copy(
                        fileId=f["id"],
                        body={"name": f["name"], "parents": [backup_id]},
                        supportsAllDrives=True, fields="id",
                    ).execute()
                summary["png_backup_folder_id"] = backup_id
                print(f"  PNG backup created → png_pre_fix_{ts}/")
        except Exception as e:
            print(f"  PNG backup failed (non-fatal): {e}")

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Processing: {folder_name}")

    # Find resources/ subfolder
    resources_folder = _find_file(drive, folder_id, "resources")
    if not resources_folder:
        print(f"  No resources/ folder — skipping")
        summary["skipped"] += 1
        return summary

    resources_id = resources_folder["id"]

    # Read media_provenance.json — if missing, build synthetic provenance from filenames
    prov_file = _find_file(drive, resources_id, "media_provenance.json")
    prov = None
    if prov_file:
        try:
            prov = json.loads(_download_bytes(drive, prov_file["id"]).decode())
        except Exception as e:
            print(f"  Failed to read provenance: {e}")
            summary["errors"] += 1
            return summary

    if prov is None:
        # Old folder — no provenance file. Build synthetic slots from images/ filenames.
        # Old naming: slide{N}_{slug}.jpg  e.g. slide2_concrete_driveway_pour.jpg
        # We assume ALL body images are candidates for re-fetch (Gemini was first in old cascade).
        images_folder_pre = _find_file(drive, resources_id, "images")
        if not images_folder_pre:
            print(f"  No resources/images/ folder either — skipping")
            summary["skipped"] += 1
            return summary
        img_files = _list_files(drive, images_folder_pre["id"])
        if not img_files:
            print(f"  No images in resources/images/ — skipping")
            summary["skipped"] += 1
            return summary
        # Synthesize provenance: treat every slide image as ai-sourced for re-fetch attempt
        prov = {"cover": {}, "slides": {}}
        for f in img_files:
            m = re.match(r"slide(\d+)_(.+)\.(jpg|jpeg|png)$", f["name"], re.IGNORECASE)
            if not m:
                continue
            slide_n = int(m.group(1))
            slug = m.group(2).replace("_", " ")
            entry = {"source_type": "ai", "provider": "gemini-legacy",
                     "query": slug, "prompt": "", "path": f"resources/images/{f['name']}"}
            if slide_n == 1:
                prov["cover"] = entry
            else:
                prov["slides"][str(slide_n)] = entry
        print(f"  No provenance — synthesized {len(img_files)} slot(s) from filenames (legacy folder)")

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
                "old_provider": cover.get("provider", ""),
                "old_path": cover.get("path", ""),
            })

    for slide_key, slide_data in prov.get("slides", {}).items():
        if isinstance(slide_data, dict) and slide_data.get("source_type") == "ai":
            ai_slots.append({
                "slot": f"slide_{slide_key}",
                "slide_num": int(slide_key),
                "query": slide_data.get("query", ""),
                "prompt": slide_data.get("prompt", ""),
                "subject_type": slide_data.get("subject_type", "place"),
                "old_provider": slide_data.get("provider", ""),
                "old_path": slide_data.get("path", ""),
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
        old_provider = slot.get("old_provider", "")
        old_path = slot.get("old_path", "")

        # Auto-fixer: when the reviewer already knows which provider produced the
        # bad image, skip it on the retry so the cascade picks something else.
        skip_for_slot = skip_provider_per_slot.get(slot_label) or old_provider
        skip_list = [skip_for_slot] if skip_for_slot else []

        print(f"  → {slot_label}: query='{query[:60]}' (skip={skip_for_slot or 'none'})")

        if dry_run:
            summary["details"].append({
                "slot": slot_label, "action": "would_fix",
                "old_provider": old_provider, "old_query": query,
                "old_path": old_path,
            })
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
        # Generate separate Pixabay/Pexels query — short visual terms, not technical product names
        stock_q = _build_stock_query(slide_text, query, niche)
        img_path, used_provider = fetch_real_photo(query, str(local_dir), filename, stock_query=stock_q)
        source_type = "cc" if used_provider == "wikimedia" else ("stock" if used_provider else "")

        # Duplicate guard: if same bytes as another slot in this carousel, reject and
        # fall through to AI cascade (Pixabay returns the same top image for similar queries)
        if img_path:
            local_candidate = local_dir / "resources" / "images" / filename
            if local_candidate.exists():
                img_hash = hashlib.md5(local_candidate.read_bytes()).hexdigest()
                if img_hash in seen_hashes:
                    print(f"    Duplicate image detected (same as previous slot) → retrying with AI cascade")
                    local_candidate.unlink()
                    img_path, used_provider, source_type = None, "", ""
                else:
                    seen_hashes.add(img_hash)

        # Step 3: AI cascade if real photos miss or produced a duplicate
        if not img_path and subject_type != "person":
            img_path, used_provider = generate_ai_image(
                fresh_prompt, str(local_dir), filename, provider,
                skip_providers=skip_list,
            )
            source_type = "ai" if img_path else ""
            # Duplicate guard for AI results too
            if img_path:
                local_candidate = local_dir / "resources" / "images" / filename
                if local_candidate.exists():
                    img_hash = hashlib.md5(local_candidate.read_bytes()).hexdigest()
                    if img_hash in seen_hashes:
                        print(f"    AI result is also a duplicate — all tiers exhausted for {slot_label}")
                        local_candidate.unlink()
                        img_path, used_provider, source_type = None, "", ""
                    else:
                        seen_hashes.add(img_hash)

        if not img_path:
            print(f"    All tiers failed for {slot_label}")
            summary["errors"] += 1
            summary["details"].append({
                "slot": slot_label, "action": "failed",
                "old_provider": old_provider, "old_query": query,
                "old_path": old_path,
            })
            continue

        # Step 4: Upload to Drive images/ folder
        local_img = local_dir / "resources" / "images" / filename
        if not images_id:
            images_id = _find_or_create_folder(drive, resources_id, "images")
        _upload_file(drive, local_img, images_id, filename)
        print(f"    ✅ Fixed via {used_provider} → {filename}")

        # Move old image to images/replaced/ so the main folder stays clean
        # (old DALL-E / legacy images won't be mistaken for current ones)
        if old_path:
            old_filename = old_path.split("/")[-1]
            if old_filename and old_filename != filename:
                _move_to_replaced(drive, images_id, old_filename)

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
            "old_provider": old_provider, "old_query": query, "old_path": old_path,
            "new_provider": used_provider, "new_query": query,
            "new_path": rel_path, "new_source_type": source_type,
            "filename": filename, "fresh_prompt": fresh_prompt,
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
