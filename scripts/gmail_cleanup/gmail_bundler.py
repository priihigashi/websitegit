"""
Bundler — runs after inbox_cleanup_rules.py
Groups same-topic inbox items into a single ⏰ bundle email (sent to herself).
Archives originals once bundled.

RULES:
- Carousel approvals: individually for 2 days, then bundle by niche
- Pipeline FAILED: individually for 12h, then bundle — auto-resolve when same workflow ✅
- Billing alerts: bundle after 5h — auto-resolve when payment success email found
"""
import re, datetime, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_log
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# People-protection: never bundle/archive emails from these patterns
PEOPLE_DOMAINS = ('gmail.com', 'oakpark-construction.com', 'mcfollingproperties.com')
NO_REPLY_HINTS = ('no-reply', 'noreply', 'donotreply', 'mailer-daemon',
                  'notifications@', 'support@', 'billing@', 'failed-payments@')

def is_human_sender(sender):
    s = (sender or '').lower()
    if any(h in s for h in NO_REPLY_HINTS):
        return False
    return any(d in s for d in PEOPLE_DOMAINS)

import tempfile, json
SELF_EMAIL  = 'priscila@oakpark-construction.com'
_LOCAL_TOKEN = '/Users/priscilahigashi/ClaudeWorkspace/Credentials/sheets_token.json'
if os.path.exists(_LOCAL_TOKEN):
    _token_path = _LOCAL_TOKEN
else:
    raw = os.environ.get('SHEETS_TOKEN', '')
    if not raw:
        raise RuntimeError('No credentials: set SHEETS_TOKEN env or have local file')
    _tf = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    _tf.write(raw); _tf.close()
    _token_path = _tf.name

creds = Credentials.from_authorized_user_file(_token_path)
gmail = build('gmail', 'v1', credentials=creds)
now   = datetime.datetime.now()

cutoff_5h   = now - datetime.timedelta(hours=5)
cutoff_12h  = now - datetime.timedelta(hours=12)
cutoff_2d   = now - datetime.timedelta(days=2)
cutoff_30d  = now - datetime.timedelta(days=30)

L_BILLING   = 'Label_7'
L_ERRORS    = 'Label_5'
L_PA_OPC    = 'Label_24'
L_PA_NEWS   = 'Label_25'
L_REMINDER  = 'Label_22'  # ⏰ reminder bundles

# -----------------------------------------------------------------------
def search_threads(q):
    results, page = [], None
    while True:
        r = gmail.users().threads().list(userId='me', q=q, maxResults=500, pageToken=page).execute()
        results.extend(r.get('threads', []))
        page = r.get('nextPageToken')
        if not page: break
    return results

def thread_meta(tid):
    full = gmail.users().threads().get(userId='me', id=tid, format='metadata',
        metadataHeaders=['Subject', 'From']).execute()
    msgs = full.get('messages', [])
    ids  = [m['id'] for m in msgs]
    ts   = max(int(m.get('internalDate', '0')) for m in msgs) if msgs else 0
    dt   = datetime.datetime.fromtimestamp(ts / 1000)
    first_h = {}
    if msgs:
        first_h = {x['name']: x['value']
                   for x in msgs[0].get('payload', {}).get('headers', [])}
    return len(msgs), ids, dt, first_h.get('Subject', ''), first_h.get('From', '')

def send_bundle_email(subject, body):
    """Send an email from SELF_EMAIL to SELF_EMAIL via GitHub Actions send_email.yml"""
    # Escape quotes in body
    safe_body = body.replace("'", "'\\''")
    safe_subj = subject.replace("'", "'\\''")
    cmd = (
        f"~/bin/gh workflow run send_email.yml "
        f"--repo priihigashi/oak-park-ai-hub "
        f"-f to='{SELF_EMAIL}' "
        f"-f subject='{safe_subj}' "
        f"-f body='{safe_body}'"
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, executable='/bin/zsh')
    if result.returncode != 0:
        print(f"    ⚠️  send failed: {result.stderr.strip()[:120]}")
        return False
    return True

def archive_msgs(msg_ids, reason='bundled'):
    for i in range(0, len(msg_ids), 1000):
        gmail.users().messages().batchModify(userId='me',
            body={'ids': msg_ids[i:i+1000], 'removeLabelIds': ['INBOX', 'UNREAD']}).execute()
    for mid in msg_ids:
        audit_log.log('archive', mid, '', '', '', f'bundler:{reason}', 'YES')

def find_existing_bundle(subj_prefix):
    """Return (thread_id, subject, body_snippet) of an existing ⏰ bundle, or None."""
    threads = search_threads(f'in:inbox from:me subject:"{subj_prefix}"')
    for t in threads:
        _, ids, dt, subj, _ = thread_meta(t['id'])
        if subj.startswith(subj_prefix):
            return t['id'], subj, ids
    return None, None, None

# -----------------------------------------------------------------------
# BUNDLE 1: Billing alerts — after 5h
# -----------------------------------------------------------------------
print("=" * 70)
print("BUNDLE: Billing alerts (>5h old)")
print("=" * 70)

BILLING_SENDERS = [
    'mail.anthropic.com', 'failed-payments@mail.anthropic.com',
    'replicate.com', 'apify.com', 'payyourplan.com'
]
BILLING_SUBJECTS = [
    'unsuccessful', 'payment failed', 'action needed', 'low balance',
    'billing', 'overdue', 'add credits', 'credit'
]

billing_threads = search_threads(
    'in:inbox (from:failed-payments@mail.anthropic.com OR from:apify.com OR from:replicate.com '
    'OR from:payyourplan.com OR subject:unsuccessful OR subject:"payment failed" '
    'OR subject:"add credits" OR subject:"low balance")'
)
to_bundle_billing = []
already_resolved_billing = []

for t in billing_threads:
    n, ids, dt, subj, sender = thread_meta(t['id'])
    if n > 1: continue  # has reply
    if is_human_sender(sender):
        audit_log.log('protect', '', t['id'], subj, sender, 'human-sender skip', 'N/A')
        continue
    if dt < cutoff_5h:
        # Check if a success/payment-confirmed email arrived after this one
        s_lower = sender.lower()
        service = None
        for svc in ['anthropic', 'replicate', 'apify', 'payyourplan']:
            if svc in s_lower or svc in subj.lower():
                service = svc
                break
        resolved = False
        if service:
            # Stricter: require specific success phrases, not just any "payment" email
            success_q = (
                f'from:{service} '
                f'(subject:"payment received" OR subject:"payment successful" '
                f'OR subject:"your receipt" OR subject:"thank you for your payment") '
                f'after:{dt.strftime("%Y/%m/%d")}'
            )
            success_threads = search_threads(success_q)
            if success_threads:
                resolved = True
        if resolved:
            already_resolved_billing.append((subj, sender, ids))
        else:
            to_bundle_billing.append((dt, subj, sender, ids))

if to_bundle_billing:
    lines = ["Items needing attention:\n"]
    all_ids = []
    for dt, subj, sender, ids in sorted(to_bundle_billing):
        lines.append(f"❌ {subj[:70]}\n   From: {sender}\n   Date: {dt.strftime('%b %d')}\n")
        all_ids.extend(ids)
    lines.append("\nReply to original emails or add credits to resolve each item.")
    body  = "\n".join(lines)
    subj  = f"⏰ [BILLING] {len(to_bundle_billing)} item(s) need attention — {now.strftime('%b %d')}"
    ok    = send_bundle_email(subj, body)
    if ok:
        archive_msgs(all_ids)
        print(f"  Bundled {len(to_bundle_billing)} billing items → sent bundle email, archived originals")
    else:
        print(f"  Could not send bundle email — originals left in inbox")
else:
    print("  No billing items ready to bundle")

# Auto-archive resolved billing items
if already_resolved_billing:
    for subj, _, ids in already_resolved_billing:
        archive_msgs(ids)
        print(f"  ✅ Auto-resolved + archived: {subj[:60]}")

# -----------------------------------------------------------------------
# BUNDLE 2: Pipeline FAILED — after 12h
# -----------------------------------------------------------------------
print()
print("=" * 70)
print("BUNDLE: Pipeline FAILED (>12h old)")
print("=" * 70)

failed_threads = search_threads('in:inbox subject:"FAILED — run"')
to_bundle_failed = []
auto_resolved = []

for t in failed_threads:
    n, ids, dt, subj, sender = thread_meta(t['id'])
    if n > 1: continue
    if is_human_sender(sender):
        audit_log.log('protect', '', t['id'], subj, sender, 'human-sender skip', 'N/A')
        continue
    if dt < cutoff_12h:
        # Extract workflow name from subject: "🚨 ads_pulse.yml FAILED — run XXXXXX"
        m = re.search(r'(\S+\.yml)\s+FAILED', subj)
        workflow = m.group(1) if m else None
        resolved = False
        if workflow:
            # Check GitHub Actions for a successful run of this workflow AFTER the failure
            check_cmd = (
                f"~/bin/gh run list --repo priihigashi/oak-park-ai-hub "
                f"--workflow {workflow} --status success --limit 5 --json createdAt "
                f"--jq '.[].createdAt'"
            )
            r = subprocess.run(check_cmd, shell=True, capture_output=True, text=True, executable='/bin/zsh')
            if r.returncode == 0 and r.stdout.strip():
                for ts_str in r.stdout.strip().splitlines():
                    try:
                        run_dt = datetime.datetime.strptime(ts_str[:19], '%Y-%m-%dT%H:%M:%S')
                        if run_dt > dt:
                            resolved = True
                            break
                    except Exception:
                        pass
        if resolved:
            auto_resolved.append((subj, ids))
        else:
            to_bundle_failed.append((dt, subj, workflow or '?', ids))

if to_bundle_failed:
    lines = ["Still failing — no successful run found after failure:\n"]
    all_ids = []
    for dt, subj, wf, ids in sorted(to_bundle_failed):
        lines.append(f"❌ {wf}  (failed {dt.strftime('%b %d %H:%M')})\n   {subj[:70]}\n")
        all_ids.extend(ids)
    body = "\n".join(lines)
    subj = f"⏰ [PIPELINE] {len(to_bundle_failed)} workflow(s) still failing — {now.strftime('%b %d')}"
    ok   = send_bundle_email(subj, body)
    if ok:
        archive_msgs(all_ids)
        print(f"  Bundled {len(to_bundle_failed)} FAILED workflows → sent bundle, archived originals")
    else:
        print(f"  Could not send bundle email — originals left in inbox")
else:
    print("  No pipeline failures ready to bundle")

if auto_resolved:
    for subj, ids in auto_resolved:
        archive_msgs(ids)
        print(f"  ✅ Auto-resolved (successful run found) + archived: {subj[:60]}")

# -----------------------------------------------------------------------
# BUNDLE 3: Carousel approvals — after 2 days, bundle by niche
# -----------------------------------------------------------------------
print()
print("=" * 70)
print("BUNDLE: Carousel approvals (>2d old) → bundle by niche")
print("=" * 70)

for label_id, niche, subj_kw in [
    (L_PA_OPC,  'OPC',  'opc-'),
    (L_PA_NEWS, 'News', 'brazil-'),
]:
    # Use labelIds= (not query) so label ID resolves correctly
    all_msgs, page = [], None
    while True:
        r = gmail.users().messages().list(userId='me', labelIds=[label_id, 'INBOX'], maxResults=500, pageToken=page).execute()
        all_msgs.extend(r.get('messages', []))
        page = r.get('nextPageToken')
        if not page: break
    # Deduplicate to threads
    seen_threads = set()
    niche_threads = []
    for m in all_msgs:
        full = gmail.users().messages().get(userId='me', id=m['id'], format='metadata').execute()
        tid = full.get('threadId')
        if tid and tid not in seen_threads:
            seen_threads.add(tid)
            niche_threads.append({'id': tid})
    to_bundle = []
    for t in niche_threads:
        n, ids, dt, subj, _ = thread_meta(t['id'])
        if n > 1: continue
        if dt < cutoff_2d:
            to_bundle.append((dt, subj, ids))
    if not to_bundle:
        print(f"  [{niche}] none ready to bundle")
        continue
    lines = [f"{niche} carousels waiting for approval:\n"]
    all_ids = []
    for dt, subj, ids in sorted(to_bundle):
        lines.append(f"⏳ {subj[:70]}  ({dt.strftime('%b %d')})")
        all_ids.extend(ids)
    lines.append(f"\nReply 'approved' to any carousel to auto-schedule it.")
    body = "\n".join(lines)
    bsubj = f"⏰ {niche} content approvals pending — {len(to_bundle)} waiting"
    existing_tid, existing_subj, existing_ids = find_existing_bundle(f"⏰ {niche} content approvals")
    if existing_tid:
        # Archive the old bundle + send updated one
        archive_msgs(existing_ids)
        print(f"  [{niche}] updated existing bundle (archived old)")
    ok = send_bundle_email(bsubj, body)
    if ok:
        archive_msgs(all_ids)
        print(f"  [{niche}] bundled {len(to_bundle)} items → sent, archived originals")
    else:
        print(f"  [{niche}] send failed — left in inbox")

# -----------------------------------------------------------------------
# CLEANUP: Archive bundle emails older than 30d
# -----------------------------------------------------------------------
old_bundles = search_threads(f'in:inbox from:me subject:"⏰" older_than:30d')
if old_bundles:
    old_ids = []
    for t in old_bundles:
        _, ids, _, _, _ = thread_meta(t['id'])
        old_ids.extend(ids)
    archive_msgs(old_ids)
    print(f"\nArchived {len(old_ids)} stale bundle emails (>30d)")

print()
inbox_final = gmail.users().labels().get(userId='me', id='INBOX').execute()
print(f"FINAL inbox: {inbox_final.get('messagesTotal')} total, {inbox_final.get('messagesUnread')} unread")
n_logged = audit_log.flush()
print(f"AUDIT: wrote {n_logged} rows to '📝 Inbox Audit' tab")
