"""Shared audit logger for inbox cleanup + bundler scripts.
Writes every action (archive/trash/bundle/star) to the
'📝 Inbox Audit' tab in Ideas & Inbox so Priscila can search later.
"""
import datetime, os, json, tempfile
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SS_ID = '1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU'
TAB   = '📝 Inbox Audit'

_LOCAL = '/Users/priscilahigashi/ClaudeWorkspace/Credentials/sheets_token.json'
if os.path.exists(_LOCAL):
    _path = _LOCAL
else:
    raw = os.environ.get('SHEETS_TOKEN', '')
    if not raw:
        raise RuntimeError('No credentials: set SHEETS_TOKEN env or have local file')
    _tf = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    _tf.write(raw); _tf.close()
    _path = _tf.name

_creds = Credentials.from_authorized_user_file(_path)
_sheets = build('sheets', 'v4', credentials=_creds)

_buffer = []


def log(action, msg_id, thread_id, subject, sender, reason, recoverable):
    """Buffer an audit row. Flush at end of run for efficiency."""
    _buffer.append([
        datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
        action,
        msg_id or '',
        thread_id or '',
        (subject or '')[:120],
        (sender or '')[:80],
        reason or '',
        recoverable,
    ])


def flush():
    if not _buffer:
        return 0
    _sheets.spreadsheets().values().append(
        spreadsheetId=SS_ID,
        range=f"'{TAB}'!A:H",
        valueInputOption='RAW',
        insertDataOption='INSERT_ROWS',
        body={'values': _buffer},
    ).execute()
    n = len(_buffer)
    _buffer.clear()
    return n
