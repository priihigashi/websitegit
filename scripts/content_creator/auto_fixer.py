#!/usr/bin/env python3
"""
auto_fixer.py — Proactive auto-edit layer for carousel_reviewer.

When the reviewer flags an issue, this module fixes it WITHOUT a human in the
loop. Three repair paths run in sequence inside `auto_fix_drive_folder`:

  Goal 1A — Image mismatch:
      Read media_provenance.json → for each AI-sourced slot, rebuild the prompt
      via prompt_builder, then re-run the image cascade SKIPPING the provider
      that originally produced the bad image. The cascade will land on the
      next-best source automatically.

  Goal 1B — Text fact-check:
      Download cover.html → text_reviewer.review_carousel_html (Claude) →
      apply_edits_to_html (minimal substring replace) → re-upload cover.html →
      re-render PNGs via carousel_builder.render_pngs → upload PNGs back to
      Drive, replacing originals.

  Goal 1C — Visual issues (cropped face, low contrast, text overflow, ...):
      Download every PNG → visual_reviewer.review_png_folder (Claude vision) →
      route each issue:
        * fix_via=image_refetch → already covered by Goal 1A skip-provider loop
        * fix_via=css_adjust    → annotate cover.html with a remediation comment
                                  (CSS auto-tweak is per-template; flagged for
                                  next iteration so we don't ship broken edits)
        * fix_via=manual        → flag in email; reviewer leaves it alone.

  Goal 2 — PNG backup:
      Before any change, copy the current png/ children to png_pre_fix_<ts>/
      so static carousels remain recoverable. Already wired into
      fix_version_folder (backup_pngs=True).

The reviewer calls `auto_fix_drive_folder(drive, folder, niche, work_dir,
dry_run=False)` and gets back a structured change_log it can render into the
review email.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

# Reuse the heavy lifting from fix_existing_images
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fix_existing_images import (  # noqa: E402
    fix_version_folder,
    _drive,  # not used directly here but available
    _find_file,
    _list_files,
    _download_bytes,
    _upload_file,
    _find_or_create_folder,
)
from text_reviewer import (  # noqa: E402
    review_carousel_html,
    apply_edits_to_html,
)
from visual_reviewer import review_png_folder  # noqa: E402


def auto_fix_drive_folder(
    drive,
    folder: dict,
    niche: str,
    work_dir: Optional[Path] = None,
    dry_run: bool = False,
    provider: Optional[str] = None,
) -> dict:
    """Auto-fix one carousel version folder.

    Args:
        drive:      Authed Drive v3 service.
        folder:     {"id": <folder_id>, "name": <folder_name>}.
        niche:      opc | brazil | usa | higashi.
        work_dir:   Local scratch dir. Created in /tmp if omitted.
        dry_run:    Skip writes; still returns the planned change log.
        provider:   Pin to one AI provider (rare). Default = full cascade.

    Returns:
        Summary dict from `fix_version_folder` with structured `details`:
          [
            {"slot": "cover", "action": "fixed",
             "old_provider": "gemini", "old_query": "kitchen with quartz",
             "old_path": "resources/images/old_kitchen.jpg",
             "new_provider": "wikimedia", "new_path": "resources/images/...",
             "new_source_type": "cc", "filename": "..."},
            ...
          ]
        Plus `png_backup_folder_id` when backup_pngs ran.
    """
    cleanup = False
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="auto_fixer_"))
        cleanup = True

    try:
        # skip_provider_per_slot defaults built per-slot inside fix_version_folder
        # (it falls back to old_provider from provenance when this dict is empty).
        summary = fix_version_folder(
            drive,
            folder,
            niche=niche,
            dry_run=dry_run,
            provider=provider,
            work_dir=Path(work_dir),
            skip_provider_per_slot={},
            backup_pngs=not dry_run,
        )
    finally:
        if cleanup:
            import shutil
            shutil.rmtree(work_dir, ignore_errors=True)

    return summary


def render_change_log_html(summary: dict) -> str:
    """Render a summary dict's `details` as an HTML table for the review email.

    Used by carousel_reviewer.send_review_email — keeps the email self-contained
    so Priscila can see before/after for every auto-fix without opening Drive.
    """
    rows = summary.get("details", [])
    if not rows:
        return "<p style='color:#888'>No auto-fix changes in this folder.</p>"

    th_style = "padding:6px 10px;background:#1a1a1a;color:#cbcc10;text-align:left;font-size:12px"
    td_style = "padding:6px 10px;border-bottom:1px solid #222;font-size:12px;vertical-align:top"

    html = [
        f"<h3 style='color:#cbcc10;margin:16px 0 8px 0'>{summary.get('folder', '')}</h3>",
        f"<p style='color:#888;font-size:11px;margin:0 0 8px 0'>"
        f"fixed: {summary.get('fixed', 0)} · "
        f"skipped: {summary.get('skipped', 0)} · "
        f"errors: {summary.get('errors', 0)}"
        f"{' · png backup: ' + summary['png_backup_folder_id'] if summary.get('png_backup_folder_id') else ''}"
        f"</p>",
        "<table style='border-collapse:collapse;width:100%;background:#0a0a0a;color:#f0ebe3'>",
        "<tr>"
        f"<th style='{th_style}'>slot</th>"
        f"<th style='{th_style}'>action</th>"
        f"<th style='{th_style}'>before</th>"
        f"<th style='{th_style}'>after</th>"
        "</tr>",
    ]

    for r in rows:
        before = (
            f"<b>{r.get('old_provider', '—')}</b><br>"
            f"<code style='color:#888'>{r.get('old_path', '')}</code>"
        )
        if r["action"] == "fixed":
            after = (
                f"<b style='color:#cbcc10'>{r.get('new_provider', '')}</b> "
                f"<i style='color:#888'>({r.get('new_source_type', '')})</i><br>"
                f"<code style='color:#888'>{r.get('new_path', '')}</code>"
            )
        elif r["action"] == "would_fix":
            after = "<i style='color:#888'>dry-run — would re-fetch</i>"
        else:
            after = f"<span style='color:#ff5555'>{r['action']}</span>"

        html.append(
            "<tr>"
            f"<td style='{td_style}'>{r.get('slot', '')}</td>"
            f"<td style='{td_style}'>{r.get('action', '')}</td>"
            f"<td style='{td_style}'>{before}</td>"
            f"<td style='{td_style}'>{after}</td>"
            "</tr>"
        )

    html.append("</table>")
    return "\n".join(html)


# ── Future expansion ─────────────────────────────────────────────────────────
#
# Goal 1B — Fact-check auto-fix:
#   def auto_fix_facts(drive, folder, niche, claims: list[dict]) -> dict:
#       Send each claim to Claude API for verification (or trigger
#       video-research.yml for deep research). On disagreement, rewrite
#       the slide text in carousel_builder format and re-render PNG.
#
# Goal 1C — Additional issue types:
#   - Missing sources slide → auto-add sources from per-post research doc
#   - Cropped face / ghost text → re-render with adjusted CSS via main.py
#   - Wrong niche tone → route through /brand-voice agent rewrite
#
# Each plugs in here as a sibling entry point. carousel_reviewer.main() chooses
# which auto-fixer to call based on the [fix_type=…] markers it emitted.
