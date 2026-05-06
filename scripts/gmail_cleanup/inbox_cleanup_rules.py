"""Final logical pass:
1. Bundle [REVIEW] approvals: OPC vs News by content. Archive >24h to label.
2. Personal classification: mom/sister/Mike/health/kids — by sender, not promos.
3. Billing: archive stale dupes >14d. Keep latest + LSA (unresolved >90d kept for visibility).
4. Pipeline FAILED >7d → archive to 🚨 Automation Errors.
5. Old duplicate [action needed] Claude API access → archive (keep latest only).
6. Deletion pass — trash old archived messages by category:
   DELETE after 14d: automation noise (L_AUTO_NOTIF, QUOTA alerts, vendor promos)
   DELETE after 30d: billing alerts, pipeline FAILED, [REVIEW] approval archives
   NEVER DELETE: Health/Insurance, Kids/School, Personal, Mike work, Government
"""
import re, datetime, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_log
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import tempfile, json
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

# Labels
L_PA_OPC      = 'Label_24'  # 🤖 Automation/Pending Approval/OPC
L_PA_NEWS     = 'Label_25'  # 🤖 Automation/Pending Approval/News
L_PERSONAL    = 'Label_2890507496714211492'  # Personal
L_KIDS        = 'Label_5097526282211443920'  # Kids
L_MIKE_WORK   = 'Label_2587579869533872138'  # Mike work
L_HEALTH      = 'Label_10'  # 🏥 Health / Insurance
L_BILLING     = 'Label_7'   # 💳 Billing
L_ERRORS      = 'Label_5'   # 🚨 Automation Errors
L_AUTO_NOTIF  = 'Label_18'  # 🤖 Automation/Notifications
L_PROMOS      = 'Label_11'  # 📬 Promos & News

now = datetime.datetime.now()
cutoff_24h  = now - datetime.timedelta(hours=24)
cutoff_7d   = now - datetime.timedelta(days=7)
cutoff_14d  = now - datetime.timedelta(days=14)
cutoff_30d  = now - datetime.timedelta(days=30)

# Labels that must NEVER be deleted
PROTECTED_LABELS = {
    'Label_10',                       # 🏥 Health / Insurance
    'Label_5097526282211443920',       # Kids
    'Label_2890507496714211492',       # Personal
    'Label_2587579869533872138',       # Mike work
    # Government emails are inside Health or Personal labels per routing above
}

def search_threads(query):
    threads = []
    page = None
    while True:
        resp = gmail.users().threads().list(userId='me', q=query, pageToken=page, maxResults=500).execute()
        threads.extend(resp.get('threads', []))
        page = resp.get('nextPageToken')
        if not page: break
    return threads

def thread_meta(tid):
    """Return (msg_count, msg_ids, latest_date, subject_of_first)."""
    full = gmail.users().threads().get(userId='me', id=tid, format='metadata',
        metadataHeaders=['Subject', 'From']).execute()
    msgs = full.get('messages', [])
    msg_ids = [m['id'] for m in msgs]
    latest_ts = max(int(m.get('internalDate', '0')) for m in msgs) if msgs else 0
    latest_dt = datetime.datetime.fromtimestamp(latest_ts / 1000)
    subj = ''
    sender = ''
    if msgs:
        h = {x['name']: x['value'] for x in msgs[0].get('payload', {}).get('headers', [])}
        subj = h.get('Subject', '')
        sender = h.get('From', '')
    return len(msgs), msg_ids, latest_dt, subj, sender

def batch_apply(msg_ids, add=None, remove=None, reason='cleanup'):
    if not msg_ids: return 0
    add = add or []
    remove = remove or []
    for i in range(0, len(msg_ids), 1000):
        chunk = msg_ids[i:i+1000]
        gmail.users().messages().batchModify(userId='me',
            body={'ids': chunk, 'addLabelIds': add, 'removeLabelIds': remove}).execute()
    # Log each
    action = 'archive' if 'INBOX' in remove else ('label' if add else 'modify')
    for mid in msg_ids:
        audit_log.log(action, mid, '', '', '',
                      f"reason={reason} add={add} remove={remove}", 'YES')
    return len(msg_ids)

# ===========================================================================
# RULE 1: [REVIEW] approval bundling
# ===========================================================================
print("=" * 72)
print("RULE 1: [REVIEW] approvals — label all, archive >24h")
print("=" * 72)
threads = search_threads('subject:"[REVIEW]"')  # all, not just inbox — to label everything
opc_label_ids = []
news_label_ids = []
opc_archive_ids = []
news_archive_ids = []
for t in threads:
    n, mids, latest, subj, _ = thread_meta(t['id'])
    if n > 1:
        continue  # has reply, skip
    s_lower = subj.lower()
    # Route by content slug
    if 'brazil-' in s_lower or 'usa-' in s_lower or '[review] brazil' in s_lower:
        news_label_ids.extend(mids)
        if latest < cutoff_24h:
            news_archive_ids.extend(mids)
    elif 'opc-' in s_lower or '[review] opc' in s_lower:
        opc_label_ids.extend(mids)
        if latest < cutoff_24h:
            opc_archive_ids.extend(mids)

# Apply labels
batch_apply(opc_label_ids, add=[L_PA_OPC])
batch_apply(news_label_ids, add=[L_PA_NEWS])
# Archive >24h (remove INBOX + UNREAD)
batch_apply(opc_archive_ids, remove=['INBOX', 'UNREAD'])
batch_apply(news_archive_ids, remove=['INBOX', 'UNREAD'])
print(f"  OPC: labeled {len(opc_label_ids)} msgs, archived {len(opc_archive_ids)} (>24h)")
print(f"  News: labeled {len(news_label_ids)} msgs, archived {len(news_archive_ids)} (>24h)")
# Threads <24h stay in inbox, but already labeled.

# ===========================================================================
# RULE 2: Personal classification — by sender, separate from promos
# ===========================================================================
print()
print("=" * 72)
print("RULE 2: Personal classification (mom, sister, Mike, health, kids)")
print("=" * 72)

def classify_inbox(query, label, name):
    """Apply label to inbox messages. Don't archive — stays in inbox until she reads."""
    threads = search_threads(f'in:inbox {query}')
    msg_ids = []
    for t in threads:
        full = gmail.users().threads().get(userId='me', id=t['id'], format='metadata').execute()
        for m in full.get('messages', []):
            msg_ids.append(m['id'])
    batch_apply(msg_ids, add=[label])
    print(f"  [{name}] tagged {len(msg_ids)} msgs (kept in inbox)")
    return len(msg_ids)

# Mom (prihigashi.m) — Personal
classify_inbox('from:prihigashi.m@gmail.com', L_PERSONAL, 'Mom (prihigashi.m)')
# Sister (Alexandra)
classify_inbox('from:alexandrahigashi2101@gmail.com', L_PERSONAL, 'Sister (Alexandra)')
# Health insurance providers
classify_inbox('from:(bcbsil.com OR eclinicalmail.com)', L_HEALTH, 'Health/Insurance')
# Schools / kids stuff (school readiness, swim coupons, focus portal)
classify_inbox('from:(focusmail.focus-sis.com OR SwimCoupon@broward.org) OR subject:("School Readiness" OR "Swim Coupon" OR "swim central")', L_KIDS, 'Kids/School')
# McFolling Properties forwards (mostly kids/school content based on what we see)
classify_inbox('from:mcfollingproperties@gmail.com subject:("School Readiness" OR "Swim" OR "School Readiness Application")', L_KIDS, 'McFolling fwd → kids')
# Other McFolling forwards (Samsung receipt, business) → Mike work (he runs the property mgmt biz)
classify_inbox('from:mcfollingproperties@gmail.com -subject:("School Readiness" OR "Swim")', L_MIKE_WORK, 'McFolling fwd → Mike work')
# Michael directly
classify_inbox('from:michael.mcfolling@gmail.com', L_MIKE_WORK, 'Michael direct')

# CMS Marketplace appeals — health/insurance
classify_inbox('from:MarketplaceAppealsCenter@cms.hhs.gov', L_HEALTH, 'CMS Marketplace appeal')

# ===========================================================================
# RULE 3: Billing — archive stale dupes >14d, keep latest + unresolved
# ===========================================================================
print()
print("=" * 72)
print("RULE 3: Billing dupes — archive >14d, keep latest actionable")
print("=" * 72)

# Anthropic failed payments — keep newest 1, archive older
threads = search_threads('in:inbox from:failed-payments@mail.anthropic.com')
all_msg = []
for t in threads:
    n, mids, latest, subj, _ = thread_meta(t['id'])
    if n == 1:
        all_msg.append((latest, mids))
all_msg.sort(reverse=True)  # newest first
# Keep newest 1, archive rest if >14d
to_archive = []
for latest, mids in all_msg[1:]:
    if latest < cutoff_14d:
        to_archive.extend(mids)
batch_apply(to_archive, add=[L_BILLING], remove=['INBOX', 'UNREAD'])
print(f"  Anthropic failed-payments: kept newest, archived {len(to_archive)} older dupes")

# Old "[action needed] Your Claude API access is turned off" — keep newest, archive others
threads = search_threads('in:inbox subject:"[action needed] Your Claude API access"')
all_msg = []
for t in threads:
    n, mids, latest, subj, _ = thread_meta(t['id'])
    if n == 1:
        all_msg.append((latest, mids))
all_msg.sort(reverse=True)
to_archive = []
for latest, mids in all_msg[1:]:
    to_archive.extend(mids)
batch_apply(to_archive, add=[L_BILLING], remove=['INBOX', 'UNREAD'])
print(f"  [action needed] Claude API: kept newest, archived {len(to_archive)} older dupes")

# Old PayYourPlan invoice >30d — likely already paid/expired
batch_apply(
    [m['id'] for m in gmail.users().messages().list(userId='me', q='in:inbox from:payyourplan.com older_than:30d').execute().get('messages', [])],
    add=[L_BILLING], remove=['INBOX', 'UNREAD']
)
print(f"  Old PayYourPlan invoices archived")

# CloudPlatform product update (not a bill, just a name change notice)
batch_apply(
    [m['id'] for m in gmail.users().messages().list(userId='me', q='in:inbox subject:"Product Update" from:CloudPlatform-noreply').execute().get('messages', [])],
    add=[L_AUTO_NOTIF], remove=['INBOX', 'UNREAD']
)
print(f"  GCP product-update notice archived (not a bill)")

# NOTE: Keeping LSA overdue (Jan 17, 2026) in inbox — possibly unpaid balance, not safe to archive
# NOTE: Keeping CloudPlatform billing TERMINATED in inbox — needs her decision

# ===========================================================================
# RULE 4: Pipeline FAILED >7d → archive to 🚨 Automation Errors
# ===========================================================================
print()
print("=" * 72)
print("RULE 4: Pipeline FAILED >7d → 🚨 Automation Errors")
print("=" * 72)
threads = search_threads('in:inbox subject:"FAILED — run"')
to_archive_errors = []
to_keep = 0
for t in threads:
    n, mids, latest, subj, _ = thread_meta(t['id'])
    if n > 1: continue
    if latest < cutoff_7d:
        to_archive_errors.extend(mids)
    else:
        to_keep += 1
batch_apply(to_archive_errors, add=[L_ERRORS], remove=['INBOX', 'UNREAD'])
print(f"  FAILED runs: archived {len(to_archive_errors)} (>7d), kept {to_keep} recent in inbox")

# ===========================================================================
# RULE 5: Other obvious noise still around
# ===========================================================================
print()
print("=" * 72)
print("RULE 5: Other obvious stale noise")
print("=" * 72)

def archive_q(query, label, name):
    threads = search_threads(f'in:inbox {query}')
    msg_ids = []
    skipped = 0
    for t in threads:
        n, mids, _, _, _ = thread_meta(t['id'])
        if n > 1:
            skipped += 1
            continue
        msg_ids.extend(mids)
    if not msg_ids:
        print(f"  [{name}] none")
        return 0
    batch_apply(msg_ids, add=[label], remove=['INBOX', 'UNREAD'])
    print(f"  [{name}] archived {len(msg_ids)}")
    return len(msg_ids)

# Old YouTube blocked from BCPS Heat (>30d)
archive_q('from:bcbsil.com older_than:30d', L_HEALTH, 'BCBS old (>30d)')
# Old Schwab "trusted device" (Nov 2025)
archive_q('from:schwab.com subject:"trusted device"', L_PROMOS, 'Schwab trusted device old')
# Old googleaistudio welcome
archive_q('from:googleaistudio-noreply older_than:30d', L_AUTO_NOTIF, 'Google AI Studio welcome')
# Mom cruise deal (promo forward, low priority)
# (leave for her — she may want to see)
# Old googleflow (already done last round but check)
archive_q('subject:"Google Flow"', L_AUTO_NOTIF, 'Google Flow')
# Old Google Ads API compliance ticket follow-up — already filed (resolved)
archive_q('subject:"Google Ads API Compliance"', L_AUTO_NOTIF, 'Old Google Ads compliance')
# Old SOVEREIGN cleanup [Exit] handoff
archive_q('subject:"SOVEREIGN cleanup"', L_AUTO_NOTIF, 'SOVEREIGN handoff')
# Re: [carousel-reviewer] reply you sent — keep this one for context (it has reply)

# ===========================================================================
# RULE 6: Deletion pass — trash old archived messages by category
# Health / Kids / Personal / Mike work / Government = archive only, NEVER deleted
# ===========================================================================
print()
print("=" * 72)
print("RULE 6: Deletion pass (trash archived messages past retention window)")
print("=" * 72)

def trash_old(label_id, cutoff, name):
    """Trash messages under label_id older than cutoff. Skips protected labels."""
    # Use labelIds= (not search query) so label ID resolves correctly
    all_msgs = []
    page = None
    while True:
        kwargs = dict(userId='me', labelIds=[label_id], maxResults=500)
        if page:
            kwargs['pageToken'] = page
        resp = gmail.users().messages().list(**kwargs).execute()
        all_msgs.extend(resp.get('messages', []))
        page = resp.get('nextPageToken')
        if not page:
            break
    trashed = 0
    skipped_protected = 0
    for m in all_msgs:
        full = gmail.users().messages().get(
            userId='me', id=m['id'], format='metadata', metadataHeaders=['Subject']
        ).execute()
        # Skip if already in trash
        label_ids = set(full.get('labelIds', []))
        if 'TRASH' in label_ids:
            continue
        if label_ids & PROTECTED_LABELS:
            skipped_protected += 1
            continue
        ts = int(full.get('internalDate', '0'))
        dt = datetime.datetime.fromtimestamp(ts / 1000)
        if dt < cutoff:
            try:
                gmail.users().messages().trash(userId='me', id=m['id']).execute()
                trashed += 1
                audit_log.log('trash', m['id'], '', '', '',
                              f"label={label_id} >cutoff", 'YES (30d in Trash)')
            except Exception as e:
                print(f"    trash {m['id']} failed: {e}")
    print(f"  [{name}] trashed {trashed}, protected {skipped_protected} (out of {len(all_msgs)} checked)")

# 14-day: automation noise
trash_old('Label_18', cutoff_14d, '🤖 Automation/Notifications >14d')    # L_AUTO_NOTIF
trash_old('Label_23', cutoff_14d, 'QUOTA_EXCEEDED alerts >14d')          # L_23 = ALERTS Quota

# 30-day: billing, pipeline errors, approval archives
trash_old('Label_7',  cutoff_30d, '💳 Billing >30d')                     # L_BILLING
trash_old('Label_5',  cutoff_30d, '🚨 Automation Errors >30d')           # L_ERRORS
trash_old('Label_24', cutoff_30d, 'Pending Approval/OPC >30d')           # L_PA_OPC
trash_old('Label_25', cutoff_30d, 'Pending Approval/News >30d')          # L_PA_NEWS

# Also trash vendor promos older than 14d (they live under Promos & News label)
trash_old('Label_11', cutoff_14d, '📬 Promos & News >14d')               # L_PROMOS

# Final stats
print()
print("=" * 72)
inbox_after = gmail.users().labels().get(userId='me', id='INBOX').execute()
print(f"FINAL: inbox total = {inbox_after.get('messagesTotal')}, unread = {inbox_after.get('messagesUnread')}")
n_logged = audit_log.flush()
print(f"AUDIT: wrote {n_logged} rows to '📝 Inbox Audit' tab")
print("=" * 72)
