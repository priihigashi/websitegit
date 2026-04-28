#!/usr/bin/env python3
"""
carousel_reviewer.py — Post-build quality check for carousel output.
Runs automatically after content_creator.yml finishes building carousels.

Checks each built post for:
  1. Photo presence: sticker-slot has a real image (not "@..._STICKER" placeholder text)
  2. Context-image slots: "[ IMG: ... ]" placeholders not present in rendered HTML
  3. Slide count: at least 5 slides built (OPC) or 4 slides (Brazil/USA)
  4. PNG size sanity: every PNG > 10KB (blank-slide detection)
  5. Motion folder: at least 1 MP4 present

Reports via email if any check fails. Exits with code 0 always (non-blocking).

Usage:
  python carousel_reviewer.py   ← reads CONTENT_CREATOR_RUN env var (JSON list of results)
  python carousel_reviewer.py --dry-run  ← print checks without emailing
"""

import json, os, re, subprocess, sys
from pathlib import Path
from datetime import datetime
import urllib.request, urllib.parse
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# Env vars
SHEETS_TOKEN     = os.environ.get("SHEETS_TOKEN", "")
ALERT_EMAIL      = os.environ.get("ALERT_EMAIL", "priscila@oakpark-construction.com")
RUN_RESULTS_JSON = os.environ.get("CONTENT_CREATOR_RUN", "[]")  # JSON array of result dicts
REVIEW_DRIVE_FOLDERS = os.environ.get("REVIEW_DRIVE_FOLDERS", "").strip()  # CSV folder ids or links

DRY_RUN = "--dry-run" in sys.argv


# ─── Checks ──────────────────────────────────────────────────────────────────

def check_html_placeholders(html_path: str) -> list[str]:
    """Return list of issue strings found in the HTML file."""
    issues = []
    try:
        html = Path(html_path).read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return [f"Cannot read HTML: {e}"]

    # Placeholder sticker text — pattern: @WORD_STICKER
    placeholder_matches = re.findall(r'@\w+_STICKER', html)
    if placeholder_matches:
        issues.append(
            f"PLACEHOLDER sticker(s) found — real photo NOT embedded: {', '.join(set(placeholder_matches))}"
        )

    # Context-image slot still has query text (not replaced with real image)
    ctx_matches = re.findall(r'\[ IMG: ([^\]]{3,60}) \]', html)
    if ctx_matches:
        issues.append(
            f"CONTEXT-IMAGE slot(s) still have placeholder text — image not sourced: "
            + "; ".join(ctx_matches[:3])
        )

    # OPC-specific quality checks (prevent text-only middle slides)
    if "Tip of the Week · Oak Park Construction" in html:
        slot_count = len(re.findall(r'class="context-img-slot"', html))
        if slot_count < 3:
            issues.append(
                f"OPC layout issue: expected >=3 context image slots on slides 2-4, found {slot_count}"
            )

        img_count = len(re.findall(r'<div class="context-img-slot">\s*<img ', html))
        if img_count < 2:
            issues.append(
                f"OPC visual floor miss: only {img_count} context slot(s) have real images; require >=2"
            )
        # If there are more than 3 body slides (future longer topics), keep visual rhythm:
        # at least every other body slide should have a real image.
        if slot_count > 3:
            min_by_rhythm = (slot_count + 1) // 2
            if img_count < min_by_rhythm:
                issues.append(
                    f"OPC rhythm miss: {img_count}/{slot_count} body visuals have real images; require >= {min_by_rhythm}"
                )

        fallback_count = len(re.findall(r'class="ctx-fallback"', html))
        if fallback_count > 1:
            issues.append(
                f"OPC fallback overuse: {fallback_count} context slots still fallback text (max 1)"
            )

        if "class=\"project-note\"" not in html:
            issues.append(
                "OPC explanation missing: project-note block not found on stat slide"
            )
        # Last slide should mirror cover style with hero background.
        sources_blocks = len(re.findall(r'<div class="slide slide-sources', html))
        sources_with_bg = len(re.findall(r'<div class="slide slide-sources[^"]*">\s*<div class="bg-photo"', html))
        if sources_blocks and sources_with_bg < sources_blocks:
            issues.append("OPC last slide miss: sources slide is missing hero background image block.")

    return issues


def check_png_folder(png_dir: str, min_slides: int = 4) -> list[str]:
    """Check PNG output folder for count + size sanity."""
    issues = []
    if not Path(png_dir).exists():
        return [f"PNG folder missing: {png_dir}"]

    pngs = sorted(Path(png_dir).glob("*.png"))
    if len(pngs) < min_slides:
        issues.append(f"Too few PNGs: {len(pngs)} found, expected ≥ {min_slides}")

    tiny = [p.name for p in pngs if p.stat().st_size < 10_000]
    if tiny:
        issues.append(f"Suspiciously small PNGs (blank slide?): {', '.join(tiny)}")

    return issues


def check_motion_folder(motion_dir: str) -> list[str]:
    """Check that at least 1 MP4 was rendered."""
    if not Path(motion_dir).exists():
        return ["Motion folder missing entirely"]
    mp4s = list(Path(motion_dir).glob("*.mp4"))
    if not mp4s:
        return ["No MP4 files in motion folder — motion render failed"]
    return []


def _extract_drive_id(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{20,}", text):
        return text
    return ""


def _build_drive_service():
    if not SHEETS_TOKEN:
        return None
    creds = Credentials.from_authorized_user_info(json.loads(SHEETS_TOKEN))
    return build("drive", "v3", credentials=creds)


def _list_children(drive, folder_id: str, mime: str | None = None):
    q = f"'{folder_id}' in parents and trashed=false"
    if mime:
        q += f" and mimeType='{mime}'"
    return drive.files().list(
        q=q,
        fields="files(id,name,mimeType,size,webViewLink)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        corpora="allDrives",
    ).execute().get("files", [])


def _find_folder_id(drive, parent_id: str, name: str) -> str:
    q = (
        f"'{parent_id}' in parents and trashed=false and "
        f"mimeType='application/vnd.google-apps.folder' and name='{name}'"
    )
    files = drive.files().list(
        q=q,
        fields="files(id,name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        corpora="allDrives",
    ).execute().get("files", [])
    return files[0]["id"] if files else ""


def _download_drive_text(drive, file_id: str) -> str:
    req = drive.files().get_media(fileId=file_id)
    return req.execute().decode("utf-8", errors="ignore")


def _resolve_version_root(drive, folder_id: str) -> str:
    """If a child folder like png/motion/resources is passed, move up to version root."""
    meta = drive.files().get(
        fileId=folder_id, fields="id,name,parents", supportsAllDrives=True
    ).execute()
    name = (meta.get("name") or "").strip().lower()
    if name not in {"png", "motion", "resources"}:
        return folder_id
    parents = meta.get("parents") or []
    if not parents:
        return folder_id
    return parents[0]


def check_drive_folder(folder_id: str, drive, input_ref: str = "") -> dict:
    issues = []
    original_id = folder_id
    folder_id = _resolve_version_root(drive, folder_id)
    folder_meta = drive.files().get(
        fileId=folder_id, fields="id,name,webViewLink", supportsAllDrives=True
    ).execute()
    folder_name = folder_meta.get("name", folder_id)

    files = _list_children(drive, folder_id)
    html_file = next((f for f in files if f.get("name") == "cover.html"), None)
    if html_file:
        try:
            html_text = _download_drive_text(drive, html_file["id"])
            tmp = Path("/tmp") / f"review_{folder_id}.html"
            tmp.write_text(html_text, encoding="utf-8")
            issues.extend(check_html_placeholders(str(tmp)))
        except Exception as e:
            issues.append(f"Could not inspect cover.html: {e}")
    else:
        issues.append("cover.html missing in version folder")

    png_folder_id = _find_folder_id(drive, folder_id, "png")
    if not png_folder_id:
        issues.append("PNG folder missing")
    else:
        pngs = [f for f in _list_children(drive, png_folder_id) if f.get("name", "").lower().endswith(".png")]
        if len(pngs) < 5:
            issues.append(f"Too few PNGs: {len(pngs)} found, expected ≥ 5")
        tiny = [p["name"] for p in pngs if int(p.get("size") or 0) < 10_000]
        if tiny:
            issues.append(f"Suspiciously small PNGs (blank slide?): {', '.join(tiny)}")

    motion_folder_id = _find_folder_id(drive, folder_id, "motion")
    if not motion_folder_id:
        issues.append("Motion folder missing entirely")
    else:
        mp4s = [f for f in _list_children(drive, motion_folder_id) if f.get("name", "").lower().endswith(".mp4")]
        if not mp4s:
            issues.append("No MP4 files in motion folder — motion render failed")

    return {
        "post_id": folder_id,
        "topic": folder_name[:60],
        "niche": "manual",
        "issues": issues,
        "passed": len(issues) == 0,
        "drive_link": folder_meta.get("webViewLink", ""),
        "input_ref": input_ref or original_id,
        "resolved_id": folder_id,
        "original_id": original_id,
    }


def check_built_post(result: dict) -> dict:
    """Run all checks on a single built post result dict.
    Returns {post_id, topic, niche, issues: [str], passed: bool}."""
    post_id = result.get("post_id", "unknown")
    topic   = result.get("topic", "")
    niche   = result.get("niche", "")

    all_issues = []

    # 1. HTML placeholder check — look for cover.html in version folder (local path)
    # The content_creator already cleaned up work_dir, so we check Drive link heuristically.
    # In local GitHub Actions run, WORK_DIR still exists during this script's execution.
    work_dir_env = os.environ.get("WORK_DIR", "/tmp/content_creator_run")
    html_local = Path(work_dir_env) / post_id / "cover.html"
    if html_local.exists():
        all_issues.extend(check_html_placeholders(str(html_local)))
    else:
        # Try common temp pattern
        for candidate in Path(work_dir_env).glob(f"**/{post_id}/cover.html"):
            all_issues.extend(check_html_placeholders(str(candidate)))
            break
        else:
            # Work dir cleaned up — can't check HTML placeholders locally
            pass  # Drive folder link check would require downloading — skip for now

    # 2. PNG check
    png_dir_local = Path(work_dir_env) / post_id / "png"
    if png_dir_local.exists():
        min_slides = 5 if niche == "opc" else 4
        all_issues.extend(check_png_folder(str(png_dir_local), min_slides))

    # 3. Motion check
    motion_dir_local = Path(work_dir_env) / post_id / "motion"
    all_issues.extend(check_motion_folder(str(motion_dir_local)))

    passed = len(all_issues) == 0
    return {
        "post_id": post_id,
        "topic": topic[:60],
        "niche": niche,
        "issues": all_issues,
        "passed": passed,
        "drive_link": result.get("version_link") or result.get("static_link", ""),
    }


# ─── Email ────────────────────────────────────────────────────────────────────

def send_review_email(failed_posts: list[dict], all_posts: list[dict]):
    """Send review report via send_email.yml workflow."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    total = len(all_posts)
    n_fail = len(failed_posts)
    n_pass = total - n_fail

    subject = f"[carousel-reviewer] {n_pass}/{total} passed — {n_fail} issue(s) found — {now}"

    lines = [
        f"CAROUSEL REVIEW REPORT — {now}",
        f"Total built: {total} | Passed: {n_pass} | Issues: {n_fail}",
        "",
    ]

    for p in all_posts:
        status = "✅ PASS" if p["passed"] else "❌ ISSUES"
        lines.append(f"{status}  [{p['niche'].upper()}] {p['topic']}")
        lines.append(f"       Drive: {p['drive_link']}")
        for issue in p["issues"]:
            lines.append(f"       ⚠  {issue}")
        lines.append("")

    lines += [
        "─" * 60,
        "To fix sticker placeholders: source real CC photos and re-run the pipeline.",
        "The image_suggestions.txt in each post's resources/ folder lists exactly what's needed.",
        "Workflow: https://github.com/priihigashi/oak-park-ai-hub/actions/workflows/content_creator.yml",
    ]

    body = "\n".join(lines)

    if DRY_RUN:
        print("\n[DRY RUN] Would send email:")
        print(f"Subject: {subject}")
        print(body)
        return

    try:
        subprocess.run(
            [
                "gh", "workflow", "run", "send_email.yml",
                "--repo", "priihigashi/oak-park-ai-hub",
                "-f", f"to={ALERT_EMAIL}",
                "-f", f"subject={subject}",
                "-f", f"body={body}",
            ],
            check=False, timeout=30,
        )
        print(f"  Review report emailed to {ALERT_EMAIL}")
    except Exception as e:
        print(f"  Review email failed (non-fatal): {e}")
        print(body)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n[carousel-reviewer] Starting post-build review...")

    # Parse results from env var or stdin
    results_raw = RUN_RESULTS_JSON
    try:
        results = json.loads(results_raw) if results_raw.strip() else []
    except json.JSONDecodeError:
        results = []

    reviewed = []
    if results:
        print(f"  Reviewing {len(results)} post(s) from CONTENT_CREATOR_RUN...")
        reviewed.extend(check_built_post(r) for r in results)

    manual_targets = [x.strip() for x in REVIEW_DRIVE_FOLDERS.split(",") if x.strip()]
    manual_inputs = []
    for raw in manual_targets:
        fid = _extract_drive_id(raw)
        if fid:
            manual_inputs.append((raw, fid))
    if manual_inputs:
        print(f"  Reviewing {len(manual_inputs)} existing Drive folder(s) on demand...")
        drive = _build_drive_service()
        if not drive:
            print("  SHEETS_TOKEN missing — cannot review Drive folders")
        else:
            seen_resolved = set()
            for raw_ref, fid in manual_inputs:
                try:
                    result = check_drive_folder(fid, drive, input_ref=raw_ref)
                    rid = result.get("resolved_id", "")
                    if rid and rid in seen_resolved:
                        print(f"  ↪ Skipping duplicate target (same resolved folder): {raw_ref} -> {rid}")
                        continue
                    if rid:
                        seen_resolved.add(rid)
                    reviewed.append(result)
                except Exception as e:
                    reviewed.append({
                        "post_id": fid,
                        "topic": fid,
                        "niche": "manual",
                        "issues": [f"Drive review failed: {e}"],
                        "passed": False,
                        "drive_link": f"https://drive.google.com/drive/folders/{fid}",
                        "input_ref": raw_ref,
                        "resolved_id": fid,
                        "original_id": fid,
                    })

    if not reviewed:
        print("  No results to review (CONTENT_CREATOR_RUN and REVIEW_DRIVE_FOLDERS empty) — exiting")
        return

    passed = [r for r in reviewed if r["passed"]]
    failed = [r for r in reviewed if not r["passed"]]

    for r in reviewed:
        icon = "✅" if r["passed"] else "❌"
        print(f"  {icon} [{r['niche']}] {r['topic']}")
        if r.get("niche") == "manual":
            print(
                f"       input: {r.get('input_ref','')} | resolved: {r.get('resolved_id', r.get('post_id',''))}"
            )
        for issue in r["issues"]:
            print(f"       ⚠  {issue}")

    print(f"\n  Summary: {len(passed)}/{len(reviewed)} passed")

    # Always send report (even if all pass — confirms reviewer ran)
    send_review_email(failed, reviewed)

    # Always exit 0 — reviewer is informational, not blocking
    print("[carousel-reviewer] Done.")


if __name__ == "__main__":
    main()
