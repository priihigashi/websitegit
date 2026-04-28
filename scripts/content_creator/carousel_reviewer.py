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
try:
    from PIL import Image, ImageStat  # type: ignore
except Exception:
    Image = None
    ImageStat = None

# Env vars
SHEETS_TOKEN     = os.environ.get("SHEETS_TOKEN", "")
ALERT_EMAIL      = os.environ.get("ALERT_EMAIL", "priscila@oakpark-construction.com")
RUN_RESULTS_JSON = os.environ.get("CONTENT_CREATOR_RUN", "[]")  # JSON array of result dicts
REVIEW_DRIVE_FOLDERS = os.environ.get("REVIEW_DRIVE_FOLDERS", "").strip()  # CSV folder ids or links

DRY_RUN = "--dry-run" in sys.argv

STOPWORDS = {
    "the","and","for","with","this","that","from","into","your","you","are","was","have",
    "how","what","when","where","why","a","an","of","to","in","on","by","or","at","as",
    "oak","park","construction","tip","week","pro","move","list","real","number","save",
    "more","less","using","use","build","project","process","action","guide"
}
GENERIC_IMAGE_QUERY_TOKENS = {
    "construction","home","house","building","project","work","process","outdoor","indoor",
    "renovation","contractor","garden","kitchen","bathroom","tools","site","job"
}


def _tokens(text: str) -> set[str]:
    parts = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    return {p for p in parts if p not in STOPWORDS}


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
        # Cover subhead length guardrail (creator enforces <=110 chars).
        m_sub = re.search(r'<div class="body-text">([\s\S]*?)</div>', html)
        if m_sub:
            sub_txt = re.sub(r"<[^>]+>", "", m_sub.group(1)).strip()
            if len(sub_txt) > 110:
                issues.append(
                    f"OPC cover subhead too long ({len(sub_txt)} chars) — max 110 to avoid HUD overlap."
                )
        # Swipe text integrity + no clipping-prone typo patterns.
        if "WIPE →" in html:
            issues.append("OPC swipe label typo/clipping artifact detected ('WIPE →').")
        swipe_count = html.count("SWIPE →")
        if swipe_count < 4:
            issues.append(f"OPC swipe indicator missing on expected slides (found {swipe_count}, expected >=4).")
        # Ensure cover HUD lane classes are present.
        if ".slide-cover .arrow" not in html or ".slide-cover .slide-logo" not in html:
            issues.append("OPC cover HUD lane styles missing (.slide-cover .arrow / .slide-cover .slide-logo).")
        # Safe-margin check for cover HUD: avoid edge clipping.
        m_arrow = re.search(r"\.slide-cover\s+\.arrow\s*\{([\s\S]*?)\}", html)
        if m_arrow:
            m_right = re.search(r"right\s*:\s*(\d+)px", m_arrow.group(1))
            m_bottom = re.search(r"bottom\s*:\s*(\d+)px", m_arrow.group(1))
            if m_right and int(m_right.group(1)) < 56:
                issues.append(f"OPC cover swipe too close to right edge ({m_right.group(1)}px).")
            if m_bottom and int(m_bottom.group(1)) < 40:
                issues.append(f"OPC cover swipe too close to bottom edge ({m_bottom.group(1)}px).")
        m_logo = re.search(r"\.slide-cover\s+\.slide-logo\s*\{([\s\S]*?)\}", html)
        if m_logo:
            m_left = re.search(r"left\s*:\s*(\d+)px", m_logo.group(1))
            m_bottom = re.search(r"bottom\s*:\s*(\d+)px", m_logo.group(1))
            if m_left and int(m_left.group(1)) < 56:
                issues.append(f"OPC cover license too close to left edge ({m_left.group(1)}px).")
            if m_bottom and int(m_bottom.group(1)) < 40:
                issues.append(f"OPC cover license too close to bottom edge ({m_bottom.group(1)}px).")
        # Last slide should mirror cover style with hero background.
        sources_blocks = len(re.findall(r'<div class="slide slide-sources', html))
        sources_with_bg = len(re.findall(r'<div class="slide slide-sources[^"]*">\s*<div class="bg-photo"', html))
        if sources_blocks and sources_with_bg < sources_blocks:
            issues.append("OPC last slide miss: sources slide is missing hero background image block.")
        # Relevance checks: each body slide context image query should share keywords with slide copy.
        for slide_cls in ("slide-stat", "slide-list", "slide-tip"):
            m_slide = re.search(rf'<div class="slide {slide_cls}[^"]*">([\s\S]*?)</div>\s*<div class="slide', html)
            if not m_slide:
                continue
            block = m_slide.group(1)
            m_q = re.search(r'data-query="([^"]*)"', block)
            if not m_q:
                issues.append(f"OPC relevance miss: {slide_cls} context slot has no data-query metadata.")
                continue
            query = m_q.group(1).replace("&quot;", '"').strip()
            if not query:
                issues.append(f"OPC relevance miss: {slide_cls} has empty image query.")
                continue
            txt = re.sub(r"<[^>]+>", " ", block)
            q_tokens = _tokens(query)
            t_tokens = _tokens(txt)
            overlap = q_tokens.intersection(t_tokens)
            if len(q_tokens) >= 2 and len(overlap) == 0:
                issues.append(
                    f"OPC relevance miss: {slide_cls} image query appears off-topic (no keyword overlap): '{query[:80]}'"
                )
            specific_tokens = {t for t in q_tokens if len(t) >= 6 and t not in GENERIC_IMAGE_QUERY_TOKENS}
            if len(specific_tokens) < 1:
                issues.append(
                    f"OPC relevance miss: {slide_cls} image query too generic for reliable match: '{query[:80]}'"
                )
        # Text overflow/collision lint (enforce before render problems happen).
        m_hl = re.search(r'<div class="headline">([\s\S]*?)</div>', html)
        if m_hl:
            hl_txt = re.sub(r"<[^>]+>", "", m_hl.group(1)).strip()
            if len(hl_txt) > 42:
                issues.append(f"OPC cover headline too long ({len(hl_txt)} chars) — risk of overflow/collision.")
        items = re.findall(r'<div class="list-text">([\s\S]*?)</div>', html)
        for idx, it in enumerate(items, start=1):
            it_txt = re.sub(r"<[^>]+>", "", it).strip()
            if len(it_txt) > 34:
                issues.append(f"OPC list item {idx} too long ({len(it_txt)} chars) — risk of line wrap collision.")
        # Source/readability quality checks.
        sources = re.findall(
            r'<div class="src-row">\s*<span class="src-num">[\s\S]*?</span>\s*<span>([\s\S]*?)</span>\s*</div>',
            html
        )
        if len(sources) < 3:
            issues.append(f"OPC sources quality miss: only {len(sources)} source line(s); require >=3.")
        for idx, src in enumerate(sources, start=1):
            s = re.sub(r"<[^>]+>", "", src).strip()
            if len(s) < 12:
                issues.append(f"OPC sources quality miss: source line {idx} too short/readability-poor.")
            if len(s) > 120:
                issues.append(f"OPC sources quality miss: source line {idx} too long ({len(s)} chars), likely tiny text.")
        # CSS readability lints (font-size floor + known low-contrast cover bug).
        def _css_px(selector: str):
            m = re.search(rf'{re.escape(selector)}\s*\{{([\s\S]*?)\}}', html)
            if not m:
                return None
            m2 = re.search(r'font-size\s*:\s*([0-9]+)px', m.group(1))
            return int(m2.group(1)) if m2 else None
        fs_tag = _css_px(".tag")
        fs_body = _css_px(".body-text")
        fs_src = _css_px(".src-row")
        if fs_tag is not None and fs_tag < 16:
            issues.append(f"OPC readability miss: .tag font-size too small ({fs_tag}px).")
        if fs_body is not None and fs_body < 28:
            issues.append(f"OPC readability miss: .body-text font-size too small ({fs_body}px).")
        if fs_src is not None and fs_src < 20:
            issues.append(f"OPC readability miss: .src-row font-size too small ({fs_src}px).")
        if ".v2.slide-cover .headline" in html:
            m_v2 = re.search(r"\.v2\.slide-cover\s+\.headline[^{]*\{([\s\S]*?)\}", html)
            if m_v2 and "#0A0A0A" in m_v2.group(1):
                issues.append("OPC readability miss: v2 cover headline uses near-black color over dark overlay.")

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

    # Rendered PNG QA: dimension + blankness check.
    if Image is not None and ImageStat is not None:
        for p in pngs:
            try:
                with Image.open(p) as im:
                    w, h = im.size
                    if (w, h) != (1080, 1350):
                        issues.append(f"{p.name}: wrong dimensions {w}x{h} (expected 1080x1350).")
                    stat = ImageStat.Stat(im.convert("L"))
                    mean = stat.mean[0]
                    stdv = stat.stddev[0]
                    if stdv < 8:
                        issues.append(f"{p.name}: low visual variance (stddev={stdv:.1f}) — likely flat/blank render.")
                    if mean < 10 or mean > 245:
                        issues.append(f"{p.name}: extreme brightness mean={mean:.1f} — likely rendering issue.")
            except Exception as e:
                issues.append(f"{p.name}: PNG QA read failed ({e})")

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


def _download_drive_bytes(drive, file_id: str) -> bytes:
    req = drive.files().get_media(fileId=file_id)
    return req.execute()


def _check_drive_png_bytes(name: str, raw: bytes) -> list[str]:
    issues = []
    if Image is None or ImageStat is None:
        return issues
    try:
        import io
        with Image.open(io.BytesIO(raw)) as im:
            w, h = im.size
            if (w, h) != (1080, 1350):
                issues.append(f"{name}: wrong dimensions {w}x{h} (expected 1080x1350).")
            stat = ImageStat.Stat(im.convert("L"))
            mean = stat.mean[0]
            stdv = stat.stddev[0]
            if stdv < 8:
                issues.append(f"{name}: low visual variance (stddev={stdv:.1f}) — likely flat/blank render.")
            if mean < 10 or mean > 245:
                issues.append(f"{name}: extreme brightness mean={mean:.1f} — likely rendering issue.")
    except Exception as e:
        issues.append(f"{name}: PNG QA read failed ({e})")
    return issues


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
        # Same rendered PNG QA used by local flow.
        for p in pngs:
            try:
                raw = _download_drive_bytes(drive, p["id"])
                issues.extend(_check_drive_png_bytes(p["name"], raw))
            except Exception as e:
                issues.append(f"{p.get('name','?')}: PNG download/QA failed ({e})")

    motion_folder_id = _find_folder_id(drive, folder_id, "motion")
    if not motion_folder_id:
        issues.append("Motion folder missing entirely")
    else:
        mp4s = [f for f in _list_children(drive, motion_folder_id) if f.get("name", "").lower().endswith(".mp4")]
        if not mp4s:
            issues.append("No MP4 files in motion folder — motion render failed")

    # Realism/provenance check (if manifest exists): avoid AI-only look.
    resources_folder_id = _find_folder_id(drive, folder_id, "resources")
    if resources_folder_id:
        res_files = _list_children(drive, resources_folder_id)
        prov_file = next((f for f in res_files if f.get("name") == "media_provenance.json"), None)
        if prov_file:
            try:
                prov = json.loads(_download_drive_text(drive, prov_file["id"]))
                slides = prov.get("slides", {})
                providers = [str(v.get("provider", "")).lower() for v in slides.values() if isinstance(v, dict)]
                if providers:
                    ai_count = sum(1 for p in providers if p in {"gemini", "seedream", "dall-e-3", "sdxl"})
                    ratio = ai_count / max(1, len(providers))
                    if ratio >= 0.75:
                        issues.append(
                            f"OPC realism risk: {ai_count}/{len(providers)} slide images are AI-generated "
                            f"(target is mostly real photos/stock)."
                        )
            except Exception as e:
                issues.append(f"media_provenance.json parse failed: {e}")

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
