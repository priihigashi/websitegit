#!/usr/bin/env python3
"""
main.py — 4AM Content Automation Agent, Oak Park Construction
Runs daily at 4 AM ET via GitHub Actions (cron: 0 8 * * *)

Execution order:
  1. Read Scraping Targets tab
  2. Scrape Instagram/TikTok via Apify (10k+ views filter)
  3. Pick 2 Talking Head topics + write scripts with Claude
  4. Find 3-5 Pexels B-roll clips per script
  5. Update Clip Collections tab if any topic is still collecting
  6. Append 2 rows to Content Queue tab
  7. Send ntfy.sh push notification
  8. Write run log to Runs Log tab
  9. Run pattern_learner — detect recurring issues, auto-create skills or Calendar tasks
"""
import time
import pytz
from datetime import datetime

from scraper          import scrape_all_targets
from script_generator import pick_topics_and_write_scripts
from broll_finder     import get_broll_for_script
from sheets_writer    import (
    read_scraping_targets,
    append_to_content_queue,
    update_clip_collections,
    append_run_log,
)
from notifier import notify_run_complete, notify_new_skill
import pattern_learner

et = pytz.timezone("America/New_York")


def main():
    start    = time.time()
    now_et   = datetime.now(et)
    log_pfx  = f"4AM_{now_et.strftime('%Y-%m-%d_%H%M')}"

    print(f"[{log_pfx}] ─── 4AM Content Agent starting ───")

    log = {
        "status":           "success",
        "topics_found":     0,
        "scripts_generated": 0,
        "clips_found":      0,
        "rows_added":       0,
        "apify_count":      0,
        "rejected_count":   0,
        "error":            "",
        "duration_seconds": 0,
        "notification_sent": False,
        "lessons_learned":  "",
    }

    try:
        # ── 1. Read targets ──────────────────────────────────────────────────
        print(f"[{log_pfx}] Step 1: Reading Scraping Targets...")
        targets = read_scraping_targets()
        if not targets:
            raise ValueError("Scraping Targets tab is empty. Add accounts/hashtags and retry.")

        # ── 2. Scrape ────────────────────────────────────────────────────────
        print(f"[{log_pfx}] Step 2: Scraping via Apify...")
        scraped, apify_count, rejected = scrape_all_targets(targets)
        log["apify_count"]    = apify_count
        log["rejected_count"] = rejected
        log["topics_found"]   = len(scraped)
        print(f"[{log_pfx}]   {apify_count} scraped → {len(scraped)} passed (≥10k views) / {rejected} rejected")

        if not scraped:
            raise ValueError(
                f"Zero results passed 10k views filter. "
                f"({apify_count} scraped, all rejected.) "
                "Check Scraping Targets tab — add accounts with more active content."
            )

        # ── 3. Generate scripts ──────────────────────────────────────────────
        print(f"[{log_pfx}] Step 3: Generating Talking Head scripts with Claude...")
        scripts = pick_topics_and_write_scripts(scraped)
        log["scripts_generated"] = len(scripts)
        for s in scripts:
            print(f"[{log_pfx}]   Topic: {s['topic']} (~{s.get('estimated_seconds', '?')}s)")

        # ── 4. Find B-roll ───────────────────────────────────────────────────
        print(f"[{log_pfx}] Step 4: Finding B-roll on Pexels...")
        scripts_with_broll = []
        total_clips = 0
        for s in scripts:
            clips = get_broll_for_script(s["topic"], s["script"])
            scripts_with_broll.append({"script_data": s, "broll_clips": clips})
            total_clips += len(clips)
            print(f"[{log_pfx}]   '{s['topic']}': {len(clips)} clips")
        log["clips_found"] = total_clips

        # ── 5. Update Clip Collections ───────────────────────────────────────
        print(f"[{log_pfx}] Step 5: Updating Clip Collections tab...")
        updated_rows = update_clip_collections(scripts_with_broll)
        print(f"[{log_pfx}]   {updated_rows} collection row(s) updated")

        # ── 6. Write to Content Queue ────────────────────────────────────────
        print(f"[{log_pfx}] Step 6: Appending to Content Queue...")
        rows_added = append_to_content_queue(scripts_with_broll)
        log["rows_added"] = rows_added
        print(f"[{log_pfx}]   {rows_added} row(s) added")

        # ── 7. Notify ────────────────────────────────────────────────────────
        print(f"[{log_pfx}] Step 7: Sending push notification...")
        topic_names = [s["script_data"]["topic"] for s in scripts_with_broll]
        notified    = notify_run_complete(topic_names, rows_added, total_clips)
        log["notification_sent"] = notified
        print(f"[{log_pfx}]   Notification sent: {notified}")

    except Exception as exc:
        log["status"] = "fail"
        log["error"]  = str(exc)
        log["lessons_learned"] = f"EXCEPTION: {str(exc)}"
        print(f"[{log_pfx}] ✗ ERROR: {exc}")
        notify_run_complete([], 0, 0, error=str(exc))

    finally:
        # ── 8. Write run log ─────────────────────────────────────────────────
        log["duration_seconds"] = round(time.time() - start)
        print(f"[{log_pfx}] Step 8: Writing Runs Log... ({log['duration_seconds']}s total)")
        append_run_log(log)

        # ── 9. Pattern learning ───────────────────────────────────────────────
        print(f"[{log_pfx}] Step 9: Running pattern learner...")
        try:
            pattern_learner.run(notifier_fn=notify_new_skill)
        except Exception as pe:
            print(f"[{log_pfx}] Pattern learner error (non-fatal): {pe}")

        print(f"[{log_pfx}] ─── Done ───")


if __name__ == "__main__":
    main()
