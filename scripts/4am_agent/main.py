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
from pathlib import Path
import pytz
from datetime import datetime

from scraper          import scrape_all_targets, scrape_website_articles
from script_generator import pick_topics_and_write_scripts
from broll_finder     import get_broll_for_script
from sheets_writer    import (
    read_scraping_targets,
    read_scraping_destinations,
    append_to_content_queue,
    update_clip_collections,
    append_run_log,
    save_scraped_to_inspiration_library,
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

    # Print NONNEGOTIABLES for audit trail in every run log
    nonneg_path = Path(__file__).parent.parent.parent / "NONNEGOTIABLES.md"
    if nonneg_path.exists():
        print(f"\n===== NONNEGOTIABLES.md =====")
        print(nonneg_path.read_text()[:3000])
        print("=====")

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

        # -- 1b. Scrape websites → Inspiration Library (blog/both destinations) --
        destinations = read_scraping_destinations()
        for niche, urls in targets.get("WEBSITE", {}).items():
            dest = destinations.get("WEBSITE", "blog")
            if dest in ("blog", "both"):
                for url in urls:
                    if not url.strip():
                        continue
                    try:
                        articles = scrape_website_articles(url.strip(), niche)
                        if articles:
                            added = save_scraped_to_inspiration_library(articles)
                            print(f"[{log_pfx}]   Website/{niche}/{url}: {added} articles → Inspiration Library")
                    except Exception as we:
                        print(f"[{log_pfx}]   WARNING: Website scrape failed for {url}: {we}")

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

        # -- 2b. Route verification content to Inspiration Library --
        # Items with series_override (Verificamos / Fact-Checked) go to Inspiration Library
        # for human review + topic_picker routing. OPC content continues to script generation.
        verification_items = [i for i in scraped_content if i.get("series_override")]
        opc_items = [i for i in scraped_content if not i.get("series_override")]
        if verification_items:
            try:
                added = save_scraped_to_inspiration_library(verification_items)
                print(f"[{log_pfx}]   Verification items → Inspiration Library: {added} new rows")
            except Exception as ve:
                print(f"[{log_pfx}]   WARNING: Inspiration Library write failed: {ve}")

        # -- 3. Generate scripts --
        print(f"[{log_pfx}] Step 3: Generating Talking Head scripts with Claude...")
        scripts = pick_topics_and_write_scripts(opc_items)
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


def _fetch_run_error_log(run_id: int, gh_token: str) -> str:
    """Fetch the last ~3000 chars of error lines from a failed GitHub Actions run."""
    import urllib.request
    base = f"https://api.github.com/repos/priihigashi/oak-park-ai-hub"
    hdrs = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"}
    try:
        # Get jobs for the run
        jobs_url = f"{base}/actions/runs/{run_id}/jobs"
        jobs = json.loads(urllib.request.urlopen(
            urllib.request.Request(jobs_url, headers=hdrs), timeout=10).read()).get("jobs", [])
        # Find the failed job
        failed = next((j for j in jobs if j.get("conclusion") == "failure"), jobs[0] if jobs else None)
        if not failed:
            return ""
        # Download logs (returns a zip redirect; GitHub sends text/plain for single-job logs)
        log_url = f"{base}/actions/jobs/{failed['id']}/logs"
        req = urllib.request.Request(log_url, headers=hdrs)
        try:
            log_text = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="replace")
        except Exception:
            return f"job={failed['id']} conclusion=failure (log download blocked)"
        # Extract ERROR / Traceback lines
        error_lines = [l for l in log_text.splitlines()
                       if any(k in l for k in ("ERROR", "Error", "Traceback", "Exception", "🔴", "UNCAUGHT"))]
        snippet = "\n".join(error_lines[-40:])  # last 40 error lines
        return snippet[:3000] if snippet else log_text[-2000:]
    except Exception as e:
        return f"(log fetch failed: {e})"


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
                        print(f"[{log_pfx}]   ⚠️  content_creator FAILED — fetching logs for self-healer")
                        run_id = last_run.get("id")
                        error_snippet = _fetch_run_error_log(run_id, gh_token) or f"Run ID: {run_id}"
                        _record_pipeline_failure(log_pfx, "content_creator workflow failed",
                                                 error_snippet, 0)
                        print(f"[{log_pfx}]   Error context: {error_snippet[:120]}")
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
    """Read 📥 Inbox for SYSTEM: alert rows.
    1. Check Gmail for 'done' replies → mark resolved.
    2. For any still unresolved → nag via email + ntfy.
    """
    import urllib.request, urllib.parse, base64
    from datetime import datetime, timedelta, timezone

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

    # ── 1. Check Gmail for "done" replies to any alert email ──────────────────
    after = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y/%m/%d")
    for subject_keyword in ["YouTube cookies", "YT_COOKIE_ALERT", "Action needed"]:
        query = urllib.parse.quote(f'subject:"{subject_keyword}" after:{after}')
        gmail_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?q={query}&maxResults=10"
        try:
            msgs = json.loads(urllib.request.urlopen(
                urllib.request.Request(gmail_url, headers={"Authorization": f"Bearer {token}"})).read()).get("messages", [])
        except Exception:
            continue

        for msg in msgs:
            detail_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg['id']}?format=full"
            try:
                detail = json.loads(urllib.request.urlopen(
                    urllib.request.Request(detail_url, headers={"Authorization": f"Bearer {token}"})).read())
            except Exception:
                continue

            headers = {h["name"].lower(): h["value"] for h in detail.get("payload", {}).get("headers", [])}
            if "re:" not in headers.get("subject", "").lower():
                continue

            # Extract reply body
            body = ""
            payload = detail.get("payload", {})
            def _get_text(p):
                if p.get("mimeType") == "text/plain" and p.get("body", {}).get("data"):
                    return base64.urlsafe_b64decode(p["body"]["data"]).decode("utf-8", errors="replace")
                for part in p.get("parts", []):
                    r = _get_text(part)
                    if r:
                        return r
                return ""
            body = _get_text(payload)

            # Strip quoted lines
            reply_lines = []
            for line in body.split("\n"):
                if line.strip().startswith(">") or (line.strip().startswith("On ") and "wrote:" in line):
                    break
                if line.strip():
                    reply_lines.append(line.strip())
            reply_text = " ".join(reply_lines).strip().lower()

            if reply_text in ("done", "fixed", "updated", "ok", "yes"):
                print(f"[{log_pfx}]   Reply '{reply_text}' detected → resolving alerts")
                _resolve_all_system_alerts(token)
                break

    # ── 2. Check Inbox tab for still-unresolved alerts → nag ─────────────────
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
            print(f"[{log_pfx}]   ⚠️  Unresolved alert: {alert_type} — nagging")
            from notifier import _dispatch_html_email, send
            send(title=f"⚠️ Action needed: {alert_type}",
                 message=f"{message[:280]}\n\nReply 'done' to this email when fixed.",
                 priority="high", tags="warning")
            _dispatch_html_email(
                subject=f"⚠️ Daily reminder: {alert_type}",
                html_body=(
                    f"<p>{message}</p>"
                    f"<p><strong>Reply 'done' to this email once you've fixed it</strong> "
                    f"and the reminders will stop automatically.</p>"
                ),
            )


def _resolve_all_system_alerts(token):
    """Mark all SYSTEM: action_needed rows in Inbox tab as resolved."""
    import urllib.request, urllib.parse
    sheet_id = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
    enc = urllib.parse.quote("'📥 Inbox'!A:C", safe="!:'")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{enc}"
    try:
        rows = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})).read()).get("values", [])
    except Exception:
        return
    for i, row in enumerate(rows):
        if not row or not row[0].startswith("SYSTEM:"):
            continue
        status = row[2].strip().lower() if len(row) > 2 else ""
        if status == "action_needed":
            row_num = i + 1
            enc2 = urllib.parse.quote(f"'📥 Inbox'!C{row_num}", safe="!:'")
            url2 = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{enc2}?valueInputOption=USER_ENTERED"
            urllib.request.urlopen(urllib.request.Request(url2,
                data=json.dumps({"values": [["resolved"]]}).encode(), method="PUT",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}))


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
    import urllib.request, urllib.parse
    from google.oauth2.credentials import Credentials
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
    raw = os.environ['SHEETS_TOKEN']
    td  = json.loads(raw)
    data = urllib.parse.urlencode({
        "client_id":     td["client_id"],
        "client_secret": td["client_secret"],
        "refresh_token": td["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    resp = json.loads(urllib.request.urlopen(
        "https://oauth2.googleapis.com/token", data=data
    ).read())
    creds = Credentials(
        token=resp["access_token"],
        refresh_token=td["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=td["client_id"],
        client_secret=td["client_secret"],
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
