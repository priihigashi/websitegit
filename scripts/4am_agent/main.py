#!/usr/bin/env python3
"""
main.py - 4AM Content Automation Agent, Oak Park Construction
Runs daily at 4 AM ET via GitHub Actions (cron: 0 8 * * *)

Execution order:
  1.  Read Scraping Targets tab
  2.  Scrape Instagram/TikTok via Apify (10k+ views filter) - graceful fallback if unavailable
  3.  Pick 2 Talking Head topics + write scripts with Claude
  4.  Find 3-5 Pexels B-roll clips per script
  5.  Update Clip Collections tab if any topic is still collecting
  6.  Append 2 rows to Content Queue tab
  7.  Send push notification
  8.  Write run log to Runs Log tab
  9.  Run pattern_learner - detect recurring issues, auto-create skills or Calendar tasks
  10. Push CLAUDE.md mirror to Drive
  11. Chat log reader — extract carry-forwards from recent session logs
  12. Loose end detector — stale content, overdue calendar, carry-forwards
  13. Self-healer — auto-fix failed modules or create repair tasks
"""
import json
import os
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
import runner
import chat_log_reader
import loose_end_detector
import self_healer

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
        print(f"[{log_pfx}] Step 7: Sending notifications...")
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

        # C2 fix: expose Steps 1-8 pipeline failures to self_healer
        if log["status"] == "fail" and log.get("error"):
            _record_pipeline_failure(log_pfx, log["error"], log.get("lessons_learned", ""),
                                     log.get("duration_seconds", 0))

        # -- 8b. Check content_creator pipeline + process approvals --
        print(f"[{log_pfx}] Step 8b: Checking content_creator status + email approvals...")
        _check_content_creator(log_pfx)

        # -- 8c. Check system alerts (e.g. YouTube cookie expiry) --
        print(f"[{log_pfx}] Step 8c: Checking system alerts...")
        _check_system_alerts(log_pfx)

        # -- 9. Pattern learning --
        print(f"[{log_pfx}] Step 9: Running pattern learner...")
        runner.run_module("pattern_learner", pattern_learner.run, notify_new_skill)

        # -- 10. Push CLAUDE.md mirror to Drive --
        print(f"[{log_pfx}] Step 10: Pushing CLAUDE.md mirror to Drive...")
        runner.run_module("claude_md_mirror", _push_claude_md_mirror)

        # -- 11. Chat log reader (carry-forwards from recent sessions) --
        print(f"[{log_pfx}] Step 11: Reading chat logs for carry-forwards...")
        _, chat_result = runner.run_module("chat_log_reader", chat_log_reader.run)
        carries = (chat_result or {}).get("carry_forwards", [])

        # -- 12. Loose end detector (stale tasks, overdue calendar, carry-forwards) --
        print(f"[{log_pfx}] Step 12: Detecting loose ends...")
        runner.run_module("loose_end_detector", loose_end_detector.run, carries)

        # -- 13. Self-healer (fixes failed modules or creates repair tasks) --
        print(f"[{log_pfx}] Step 13: Running self-healer...")
        runner.run_module("self_healer", self_healer.run)  # C3: removed dead run_results param

        print(f"[{log_pfx}] {runner.summary_line()}")
        print(f"[{log_pfx}] --- Done ---")


def _check_content_creator(log_pfx):
    """Check content_creator pipeline: did it run? Process any email approvals."""
    try:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'content_creator'))
        from approval_handler import process_replies

        # 1. Check if content_creator ran (via GitHub API — last workflow run)
        gh_token = os.environ.get("GH_TOKEN", os.environ.get("GITHUB_TOKEN", ""))
        if gh_token:
            import urllib.request
            url = "https://api.github.com/repos/priihigashi/oak-park-ai-hub/actions/workflows/content_creator.yml/runs?per_page=1"
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {gh_token}",
                "Accept": "application/vnd.github+json",
            })
            try:
                resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
                runs = resp.get("workflow_runs", [])
                if runs:
                    last_run = runs[0]
                    status = last_run.get("conclusion", "unknown")
                    print(f"[{log_pfx}]   content_creator last run: {status} ({last_run.get('created_at', '?')})")
                    if status == "failure":
                        print(f"[{log_pfx}]   ⚠️  content_creator FAILED — flagging for self-healer")
                        _record_pipeline_failure(log_pfx, "content_creator workflow failed",
                                                 f"Run ID: {last_run.get('id')}", 0)
                else:
                    print(f"[{log_pfx}]   content_creator has never run yet")
            except Exception as e:
                print(f"[{log_pfx}]   Could not check content_creator status: {e}")

        # 2. Process email approvals
        print(f"[{log_pfx}]   Checking for approval replies...")
        stats = process_replies()
        print(f"[{log_pfx}]   Approvals: {stats.get('approved', 0)} | Changes: {stats.get('changes', 0)} | Skipped: {stats.get('skipped', 0)}")

    except Exception as e:
        print(f"[{log_pfx}]   content_creator check failed: {e}")


def _check_system_alerts(log_pfx):
    """Read 📥 Inbox for SYSTEM: alert rows. Nag daily for any unresolved ones."""
    import urllib.request, urllib.parse
    raw = os.environ.get("SHEETS_TOKEN", "")
    if not raw:
        print(f"[{log_pfx}]   SHEETS_TOKEN missing — skipping alert check")
        return
    try:
        td = json.loads(raw)
        data = urllib.parse.urlencode({
            "client_id": td["client_id"], "client_secret": td["client_secret"],
            "refresh_token": td["refresh_token"], "grant_type": "refresh_token",
        }).encode()
        resp = json.loads(urllib.request.urlopen(
            urllib.request.Request("https://oauth2.googleapis.com/token", data=data)).read())
        token = resp["access_token"]
    except Exception as e:
        print(f"[{log_pfx}]   Alert check auth failed: {e}")
        return

    sheet_id = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
    enc = urllib.parse.quote("'📥 Inbox'!A:C", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{enc}"
    try:
        rows = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})).read()).get("values", [])
    except Exception as e:
        print(f"[{log_pfx}]   Alert tab read failed: {e}")
        return

    for row in rows:
        if not row or not row[0].startswith("SYSTEM:"):
            continue
        status = row[2].strip().lower() if len(row) > 2 else "action_needed"
        if status == "action_needed":
            alert_type = row[0].replace("SYSTEM:", "")
            message = row[1] if len(row) > 1 else alert_type
            print(f"[{log_pfx}]   ⚠️  Unresolved alert: {alert_type}")
            from notifier import _dispatch_html_email, send
            send(title=f"⚠️ Action needed: {alert_type}",
                 message=message[:300], priority="high", tags="warning")
            _dispatch_html_email(
                subject=f"⚠️ Daily reminder: {alert_type}",
                html_body=f"<p>{message}</p><p>This reminder will stop once the issue is resolved.</p>",
            )


def _record_pipeline_failure(log_pfx, error_msg, lessons, duration_s):
    """Write Steps 1-8 failure to module_failures.json so self_healer can detect it. (C2 fix)"""
    failures_path = ".github/agent_state/module_failures.json"
    os.makedirs(".github/agent_state", exist_ok=True)
    existing = {}
    if os.path.exists(failures_path):
        try:
            with open(failures_path) as f:
                existing = json.load(f)
        except Exception:
            pass
    existing["pipeline_main"] = {
        "status":     "fail",
        "error":      error_msg,
        "traceback":  lessons,
        "duration_s": duration_s,
        "ts":         datetime.now(et).isoformat(),
    }
    with open(failures_path, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"[{log_pfx}] Pipeline failure written to module_failures.json for self_healer.")


def _push_claude_md_mirror():
    """Push ~/.claude/CLAUDE.md to Drive mirror doc (nightly backup)."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build as gdrive_build

    claude_md_path = os.path.expanduser('~/.claude/CLAUDE.md')
    if not os.path.exists(claude_md_path):
        print('[mirror] CLAUDE.md not found at', claude_md_path)
        return

    with open(claude_md_path, 'r') as f:
        content = f.read()

    timestamp = datetime.now(et).strftime('%Y-%m-%d %H:%M ET')
    separator = '=' * 60
    full_text = f"""CLAUDE.MD MIRROR — auto-generated {timestamp}
Source: ~/.claude/CLAUDE.md
Do NOT edit here. Edit the local file.

{separator}

""" + content

    MIRROR_DOC_ID = '1mvg0nWNOqzyREld2EGQ1C5BIoFVjcv6jUTGtIApu0GY'
    sa_key = json.loads(os.environ['GOOGLE_SA_KEY'])
    creds = service_account.Credentials.from_service_account_info(
        sa_key,
        scopes=['https://www.googleapis.com/auth/documents',
                'https://www.googleapis.com/auth/drive']
    )
    docs = gdrive_build('docs', 'v1', credentials=creds)

    doc = docs.documents().get(documentId=MIRROR_DOC_ID).execute()
    body_content = doc.get('body', {}).get('content', [])
    end_index = body_content[-1].get('endIndex', 1) if body_content else 1

    doc_requests = []
    if end_index > 1:
        doc_requests.append({'deleteContentRange': {'range': {'startIndex': 1, 'endIndex': end_index - 1}}})
    doc_requests.append({'insertText': {'location': {'index': 1}, 'text': full_text}})

    docs.documents().batchUpdate(documentId=MIRROR_DOC_ID, body={'requests': doc_requests}).execute()
    print(f'[mirror] CLAUDE.md mirror updated ({len(content)} chars)')


if __name__ == "__main__":
    main()
