#!/usr/bin/env python3
"""
fix_existing_images.py — Retroactive image quality repair for carousel Drive folders.

Scans version folders in a Drive carousel folder, Vision-validates every existing
image, re-fetches any that don't match their slide content, re-uploads to Drive.

Flow per slot:
  1. Read media_provenance.json → collect ALL slots (ai, cc, stock)
  2. For real-photo slots (cc/stock): download existing image → run Claude Haiku Vision
     - Vision says YES (image matches content) → keep, skip
     - Vision says NO or file missing → queue for re-fetch
  3. For AI slots: always queue for re-fetch (AI images are commonly wrong/generic)
  4. Persons (subject_type=="person") skip AI and go straight to Wikimedia
  5. Re-fetch queued slots: AI cascade first, real-photo fallback
  6. Upload fixed image to Drive, archive old to images/replaced/, update provenance

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
import subprocess
from pathlib import Path
from typing import Optional

# Ensure scripts/content_creator is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from image_providers import (
    fetch_real_photo, generate_ai_image, make_filename,
    PROVIDER_NB2, DEFAULT_AI_CASCADE,
)
from prompt_builder import build_image_prompt, build_stock_query as _build_stock_query, extract_slide_texts as _extract_slide_texts
from vision_validator import validate_image as _vision_validate
from image_library import search_library, enhance_library_image, mark_used

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

    # Collect ALL provenance slots, then Vision-screen real-photo ones.
    # AI slots always re-fetch. Real-photo (cc/stock): only re-fetch if Vision rejects.
    def _make_slot(label, num, data):
        return {
            "slot": label,
            "slide_num": num,
            "query": data.get("query", ""),
            "prompt": data.get("prompt", ""),
            "subject_type": data.get("subject_type", "place"),
            "old_provider": data.get("provider", ""),
            "old_path": data.get("path", ""),
            "source_type": data.get("source_type", ""),
        }

    all_slots = []
    cover = prov.get("cover", {})
    if isinstance(cover, dict) and cover.get("source_type") and cover.get("subject_type", "place") != "person":
        all_slots.append(_make_slot("cover", 1, cover))

    for slide_key, slide_data in prov.get("slides", {}).items():
        if isinstance(slide_data, dict) and slide_data.get("source_type"):
            all_slots.append(_make_slot(f"slide_{slide_key}", int(slide_key), slide_data))

    if not all_slots:
        print(f"  No provenance slots found — skipping")
        summary["skipped"] += 1
        return summary

    # Vision-screen real-photo slots: download existing image → validate → skip if correct
    vision_tmp = work_dir / folder_name / "_vision_tmp"
    vision_tmp.mkdir(parents=True, exist_ok=True)
    fix_slots = []
    for slot in all_slots:
        src = slot["source_type"]
        old_path = slot.get("old_path", "")
        if src == "ai":
            fix_slots.append(slot)
            continue
        # Real-photo: check if existing image actually matches content
        vision_query = (slide_texts.get(slot["slide_num"], "") or slot["query"] or "").strip()
        if not old_path or not images_id:
            fix_slots.append(slot)
            continue
        old_filename = old_path.split("/")[-1]
        try:
            q_str = (f"'{images_id}' in parents and name='{old_filename}' "
                     f"and mimeType!='application/vnd.google-apps.folder' and trashed=false")
            res = drive.files().list(
                q=q_str, fields="files(id,name)",
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            existing = res.get("files", [])
            if not existing:
                print(f"  {slot['slot']}: not found in Drive → queuing for fix")
                fix_slots.append(slot)
                continue
            img_bytes = _download_bytes(drive, existing[0]["id"])
            tmp_img = vision_tmp / old_filename
            tmp_img.write_bytes(img_bytes)
            ok, reason = _vision_validate(str(tmp_img), vision_query)
            if ok:
                print(f"  {slot['slot']}: Vision OK ({src}) — keeping existing image")
            else:
                print(f"  {slot['slot']}: Vision REJECT ({src}) — {reason[:100]} → queuing")
                fix_slots.append(slot)
        except Exception as e:
            print(f"  {slot['slot']}: Vision check error ({e}) — queuing anyway")
            fix_slots.append(slot)

    if not fix_slots:
        print(f"  All {len(all_slots)} slot(s) passed Vision check — nothing to fix")
        summary["skipped"] += 1
        return summary

    print(f"  Queued {len(fix_slots)} slot(s) for re-fetch (of {len(all_slots)} total)")

    # Create local work dir for this version folder
    local_dir = work_dir / folder_name
    local_dir.mkdir(parents=True, exist_ok=True)

    for slot in fix_slots:
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

        prov_slug = provider or PROVIDER_NB2
        filename = make_filename(query or fresh_prompt[:40], prov_slug, slide_num)
        # Generate separate Pixabay/Pexels query — short visual terms, not technical product names
        stock_q = _build_stock_query(slide_text, query, niche)
        img_path, used_provider, source_type = None, "", ""

        # Vision-validation query — what we want the image to depict
        vision_query = slide_text or query

        def _accept_or_reject(candidate_path, source_label):
            """Return (img_path, provider, source_type) if image passes dedup +
            Vision check; else (None, '', '') so the cascade keeps trying."""
            local_candidate = local_dir / "resources" / "images" / filename
            if not local_candidate.exists():
                return None, "", ""
            img_hash = hashlib.md5(local_candidate.read_bytes()).hexdigest()
            if img_hash in seen_hashes:
                print(f"    {source_label} duplicate (md5 match) — rejecting")
                local_candidate.unlink()
                return None, "", ""
            ok, reason = _vision_validate(str(local_candidate), vision_query)
            if not ok:
                print(f"    {source_label} Vision REJECT — {reason[:120]}")
                local_candidate.unlink()
                return None, "", ""
            seen_hashes.add(img_hash)
            print(f"    {source_label} accepted — {reason[:120]}")
            return candidate_path, source_label, ""

        # TIER 0 (OPC only) — real jobsite photos, zero AI spend
        # Checked before library, AI, and stock so we never pay for a real photo we own.
        if niche == "opc" and not img_path:
            try:
                from photo_matcher import match_opc_photo as _opc_pm  # type: ignore
                _opc_hit = _opc_pm(query or slide_text)
                if _opc_hit and _opc_hit.get("drive_url"):
                    _fid_m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", _opc_hit["drive_url"])
                    if _fid_m:
                        _opc_bytes = _download_bytes(drive, _fid_m.group(1))
                        if _opc_bytes:
                            _opc_dest = local_dir / "resources" / "images" / filename
                            _opc_dest.parent.mkdir(parents=True, exist_ok=True)
                            _opc_dest.write_bytes(_opc_bytes)
                            _acc, _, _ = _accept_or_reject(str(_opc_dest), "opc_catalog")
                            if _acc:
                                img_path = _acc
                                used_provider = "opc_catalog"
                                source_type = "real_photo"
                                print(f"    opc_catalog: {_opc_hit.get('filename')} "
                                      f"(q={_opc_hit.get('quality')})")
            except Exception as _opc_e:
                print(f"    photo_matcher TIER 0 (non-fatal): {_opc_e}")

        # Step 2: AI cascade FIRST — produces realistic, prompt-specific images
        # (Real-photo search returns generic stock that often doesn't match technical
        # construction prompts and frequently duplicates across slides.)
        try:
            lib_hit = search_library(query or slide_text, niche)
            if lib_hit:
                lib_rel = enhance_library_image(lib_hit.get("drive_url", ""), str(local_dir), filename, slide_text or query)
                if lib_rel:
                    accepted, source_label, _ = _accept_or_reject(lib_rel, "library")
                    if accepted:
                        img_path = accepted
                        used_provider = "library"
                        source_type = "library"
                        mark_used(lib_hit.get("row_idx", 0), folder_name)
        except Exception as e:
            print(f"    library lookup failed (non-fatal): {e}")

        if not img_path and subject_type != "person":
            img_path, used_provider = generate_ai_image(
                fresh_prompt, str(local_dir), filename, provider,
                skip_providers=skip_list,
            )
            source_type = "ai" if img_path else ""
            if img_path:
                accepted, _, _ = _accept_or_reject(img_path, used_provider or "ai")
                if not accepted:
                    img_path, used_provider, source_type = None, "", ""

        # Step 3: Real-photo fallback (Wikimedia → Pexels → Pixabay)
        # Used when AI cascade fails OR for named persons (subject_type=="person")
        # where AI hallucinates faces.
        if not img_path:
            img_path, used_provider = fetch_real_photo(query, str(local_dir), filename, stock_query=stock_q)
            source_type = "cc" if used_provider == "wikimedia" else ("stock" if used_provider else "")
            if img_path:
                accepted, _, _ = _accept_or_reject(img_path, used_provider or "real-photo")
                if not accepted:
                    img_path, used_provider, source_type = None, "", ""

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

    # Detect input shape: is `--folder` a child subfolder (images/resources/png/
    # motion), a version folder, a series folder, or the top-level carousel parent?
    folder_meta = drive.files().get(
        fileId=args.folder, fields="id,name,parents", supportsAllDrives=True,
    ).execute()
    input_name = (folder_meta.get("name") or "").strip()

    # Auto-traverse up if user passed a child subfolder
    SUBFOLDER_NAMES = {"images", "resources", "png", "motion", "clips", "replaced"}
    while input_name.lower() in SUBFOLDER_NAMES:
        parents = folder_meta.get("parents") or []
        if not parents:
            break
        args.folder = parents[0]
        folder_meta = drive.files().get(
            fileId=args.folder, fields="id,name,parents", supportsAllDrives=True,
        ).execute()
        input_name = (folder_meta.get("name") or "").strip()
        print(f"  Traversed up — now scanning: {input_name} ({args.folder})")

    children = _list_folders(drive, args.folder)
    has_resources = any(c["name"] == "resources" for c in children)
    has_cover_html = bool(_find_file(drive, args.folder, "cover.html"))

    if has_resources or has_cover_html or re.match(r"v\d+_", input_name or ""):
        # Input IS a version folder — process directly.
        version_folders = [{"id": args.folder, "name": input_name or args.folder}]
        print(f"  Input detected as version folder — processing directly.")
    else:
        # Input is a parent folder — find v\d+_ children one or two levels deep.
        version_folders = [f for f in children if re.match(r"v\d+_", f["name"])]
        for tf in children:
            if not re.match(r"v\d+_", tf["name"]):
                sub = _list_folders(drive, tf["id"])
                version_folders.extend([f for f in sub if re.match(r"v\d+_", f["name"])])

    version_folders.sort(key=lambda f: f["name"])
    print(f"\nFound {len(version_folders)} version folder(s) to inspect.")

    if args.limit:
        version_folders = version_folders[:args.limit]
        print(f"  Processing first {args.limit} only (--limit flag)")

    totals = {"fixed": 0, "skipped": 0, "errors": 0}
    run_summaries = []

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
            run_summaries.append(summary)

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Done.")
    print(f"  Fixed  : {totals['fixed']}")
    print(f"  Skipped: {totals['skipped']}")
    print(f"  Errors : {totals['errors']}")

    # HTML summary email for on-demand review
    try:
        rows = []
        for s in run_summaries:
            rows.append(
                f"<tr><td style='padding:6px 8px;color:#ddd'>{s.get('folder','')}</td>"
                f"<td style='padding:6px 8px;color:#CBCC10'>{s.get('fixed',0)}</td>"
                f"<td style='padding:6px 8px;color:#aaa'>{s.get('skipped',0)}</td>"
                f"<td style='padding:6px 8px;color:#f99'>{s.get('errors',0)}</td></tr>"
            )
        html = (
            "<html><body style='background:#0a0a0a;padding:20px;font-family:Arial,sans-serif;'>"
            "<h2 style='color:#CBCC10'>Fix Existing Images — Summary</h2>"
            f"<p style='color:#ccc'>Folder: {args.folder} | Niche: {args.niche}</p>"
            "<table style='border-collapse:collapse;width:100%;max-width:900px;'>"
            "<tr><th style='text-align:left;color:#eee;padding:6px 8px'>Version Folder</th>"
            "<th style='text-align:left;color:#eee;padding:6px 8px'>Fixed</th>"
            "<th style='text-align:left;color:#eee;padding:6px 8px'>Skipped</th>"
            "<th style='text-align:left;color:#eee;padding:6px 8px'>Errors</th></tr>"
            + "".join(rows) + "</table></body></html>"
        )
        subprocess.run([
            "gh", "workflow", "run", "send_email.yml",
            "--repo", "priihigashi/oak-park-ai-hub",
            "-f", "to=priscila@oakpark-construction.com",
            "-f", f"subject=[REVIEW] fix_existing_images — fixed {totals['fixed']} — errors {totals['errors']}",
            "-f", f"html_body={html}",
        ], check=False, timeout=30)
    except Exception as e:
        print(f"  Summary email send failed (non-fatal): {e}")


if __name__ == "__main__":
    main()
