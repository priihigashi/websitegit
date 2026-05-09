# SKILL: Fix Google Sheets Range Error

## Purpose
Automatically corrects range parsing errors when accessing Google Sheets.

## Trigger
When a Google Sheets API request results in an HttpError 400 with the message "Unable to parse range."

## Actions
1. Log the error details for review.
2. Check the specified range format in the request.
3. If the range format is incorrect, attempt to correct it by ensuring the range follows the 'SheetName!A1:Z100' format.
4. Retry the request with the corrected range.

## Notes
- Ensure the sheet name and range are dynamically retrieved from a reliable source or configuration to avoid hardcoding.
- This skill assumes that the range parsing issue is due to incorrect formatting and attempts a sensible correction.