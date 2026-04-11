#!/usr/bin/env python3
"""
main.py - 4AM Content Automation Agent, Oak Park Construction
Runs daily at 4 AM ET via GitHub Actions (cron: 0 8 * * *)

Execution order:
  1. Read Scraping Targets tab
  2. Scrape Instagram/TikTok via Apify (10k+ views filter) - graceful fallback if unavailable
  3. Pick 2 Talking Head topics + write scripts with Claude
  4. Find 3-5 Pexels B-roll clips per script
  5. Update Clip Collections tab if any topic is still collecting
  6. Append 2 rows to Content Queue tab
  7. Send ntfy.sh push notification
  8. Write run log to Runs Log tab
  9. Run pattern_learner - detect recurring issues, auto-create skills or Calendar tasks
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

    print(f"[{log_pfx}] --- 4AM Content Agent starting ---")

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
        # -- 1. Read targets --
        print(f"[{log_pfx}] Step 1: Reading Scraping Targets...")
        targets = read_scraping_targets()
        if not targets:
            raise ValueError("Scraping Targets tab is empty. Add accounts/hashtags and retry.")

        # -- 2. Scrape (graceful fallback) --
        print(f"[{log_pfx}] Step 2: Scraping via Apify...")
        scraped_content = []
        scrape_error = None
        try:
            scraped_content, apify_count, rejected = scrape_all_targets(targets)
            log["apify_count"]    = apify_count
            log["rejected_count"] = rejected
            log["topics_found"]   = len(scraped_content)
            print(f"[{log_pfx}]   {apify_count} scraped -> {len(scraped_content)} passed (>=10k views) / {rejected} rejected")
            if not scraped_content:
                scrape_error = f"Zero results from Apify ({apify_count} scraped, all under 10k views)"
                print(f"[{log_pfx}]   WARNING: {scrape_error} - running in fallback mode")
        except Exception as scrape_exc:
            scrape_error = str(scrape_exc)
            print(f"[{log_pfx}]   Apify scraping failed: {scrape_error}")
            print(f"[{log_pfx}]   Continuing in fallback mode (Claude generates topics directly)")

        # -- 3. Generate scripts --
        print(f"[{log_pfx}] Step 3: Generating Talking Head scripts with Claude...")
        # Pass scraped_content (may be empty list) - script_generator handles fallback internally
        scripts = pick_topics_and_write_scripts(scraped_content)
        log["scripts_generated"] = len(scripts)
        for s in scripts:
            print(f"[{log_pfx}]   Topic: {s['topic']} (~{s.get('estimated_seconds', '?')}s)")

        if not scripts:
            raise ValueError("Script generator returned 0 scripts - check Claude API key")

        # -- 4. Find B-roll --
        print(f"[{log_pfx}] Step 4: Finding B-roll on Pexels...")
        scripts_with_broll = []
        total_clips = 0
        for s in scripts:
            clips = get_broll_for_script(s["topic"], s["script"])
            scripts_with_broll.append({"script_data": s, "broll_clips": clips})
            total_clips += len(clips)
            print(f"[{log_pfx}]   '{s['topic']}': {len(clips)} clips")
        log["clips_found"] = total_clips

        # -- 5. Update Clip Collections --
        print(f"[{log_pfx}] Step 5: Updating Clip Collections tab...")
        updated_rows = update_clip_collections(scripts_with_broll)
        print(f"[{log_pfx}]   {updated_rows} collection row(s) updated")

        # -- 6. Write to Content Queue --
        print(f"[{log_pfx}] Step 6: Appending to Content Queue...")
        rows_added = append_to_content_queue(scripts_with_broll)
        log["rows_added"] = rows_added
        print(f"[{log_pfx}]   {rows_added} row(s) added")

        # -- 7. Notify --
        print(f"[{log_pfx}] Step 7: Sending push notification...")
        topic_names = [s["script_data"]["topic"] for s in scripts_with_broll]
        notified    = notify_run_complete(topic_names, rows_added, total_clips, error=scrape_error)
        log["notification_sent"] = notified
        if scrape_error:
            log["lessons_learned"] = f"Ran in fallback mode (no Apify data): {scrape_error}"
        print(f"[{log_pfx}]   Notification sent: {notified}")

    except Exception as exc:
        log["status"] = "fail"
        log["error"]  = str(exc)
        log["lessons_learned"] = f"EXCEPTION: {str(exc)}"
        print(f"[{log_pfx}] ERROR: {exc}")
        notify_run_complete([], 0, 0, error=str(exc))

    finally:
        # -- 8. Write run log --
        log["duration_seconds"] = round(time.time() - start)
        print(f"[{log_pfx}] Step 8: Writing Runs Log... ({log['duration_seconds']}s total)")
        append_run_log(log)

        # -- 9. Pattern learning --
        print(f"[{log_pfx}] Step 9: Running pattern learner...")
        try:
            pattern_learner.run(notifier_fn=notify_new_skill)
        except Exception as pe:
            print(f"[{log_pfx}] Pattern learner error (non-fatal): {pe}")

        # -- 10. Push CLAUDE.md mirror to Drive --
        print(f"[{log_pfx}] Step 10: Pushing CLAUDE.md mirror to Drive...")
        try:
            _push_claude_md_mirror()
        except Exception as me:
            print(f"[{log_pfx}] CLAUDE.md mirror error (non-fatal): {me}")

        print(f"[{log_pfx}] --- Done ---")


def _push_claude_md_mirror():
    """Push ~/.claude/CLAUDE.md to Drive mirror doc (nightly backup)."""
    import os
    from google.oauth2 import service_account
    from googleapiclient.discovery import build as gdrive_build

    claude_md_path = os.path.expanduser('~/.claude/CLAUDE.md')
    if not os.path.exists(claude_md_path):
        print('[mirror] CLAUDE.md not found at', claude_md_path)
        return

    with open(claude_md_path, 'r') as f:
        content = f.read()

    from datetime import datetime
    import pytz
    et = pytz.timezone('America/New_York')
    timestamp = datetime.now(et).strftime('%Y-%m-%d %H:%M ET')
    full_text = f'CLAUDE.MD MIRROR — auto-generated {timestamp}
Source: ~/.claude/CLAUDE.md
Do NOT edit here. Edit the local file.

{"="*60}

' + content

    MIRROR_DOC_ID = '1mvg0nWNOqzyREld2EGQ1C5BIoFVjcv6jUTGtIApu0GY'
    sa_key = json.loads(os.environ['GOOGLE_SA_KEY'])
    creds = service_account.Credentials.from_service_account_info(
        sa_key,
        scopes=['https://www.googleapis.com/auth/documents',
                'https://www.googleapis.com/auth/drive']
    )
    docs = gdrive_build('docs', 'v1', credentials=creds)

    # Clear existing content and rewrite
    doc = docs.documents().get(documentId=MIRROR_DOC_ID).execute()
    body_content = doc.get('body', {}).get('content', [])
    end_index = body_content[-1].get('endIndex', 1) if body_content else 1

    requests = []
    if end_index > 1:
        requests.append({'deleteContentRange': {'range': {'startIndex': 1, 'endIndex': end_index - 1}}})
    requests.append({'insertText': {'location': {'index': 1}, 'text': full_text}})

    docs.documents().batchUpdate(documentId=MIRROR_DOC_ID, body={'requests': requests}).execute()
    print(f'[mirror] CLAUDE.md mirror updated ({len(content)} chars)')



if __name__ == "__main__":
    main()
