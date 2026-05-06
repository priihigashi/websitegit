"""Shared audit logger for inbox cleanup + bundler scripts.
Writes every action (archive/trash/bundle/star) to the
'📝 Inbox Audit' tab in Ideas & Inbox so Priscila can search later.
"""
import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SS_ID = '1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU'
TAB   = '📝 Inbox Audit'

_creds  = Credentials.from_authorized_user_file(
    '/Users/priscilahigashi/ClaudeWorkspace/Credentials/sheets_token.json'
)
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
