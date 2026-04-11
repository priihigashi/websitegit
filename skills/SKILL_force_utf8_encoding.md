# SKILL: Force UTF-8 Encoding for All Output

## Trigger
Before writing ANY text to a file, spreadsheet cell, API payload, or HTTP response.

## Rule
All string encoding MUST use UTF-8, never latin-1 (iso-8859-1).

When the agent generates captions, hashtags, or any text that may contain emojis or non-ASCII characters:

1. **Strip or replace dangerous characters early.** Before passing text to any I/O layer, run a sanitisation step:
   - Preferred: ensure the output stream / request header specifies `charset=utf-8`.
   - Fallback: remove non-latin-1 characters with `text.encode('utf-8', errors='ignore').decode('utf-8')`.
2. **Never use `.encode('latin-1')` anywhere in the pipeline.** If a library defaults to latin-1, override it explicitly with `encoding='utf-8'`.
3. **Common culprits to check:**
   - `open(path, 'w')` → use `open(path, 'w', encoding='utf-8')`
   - `csv.writer` → wrap the file handle with utf-8 encoding
   - HTTP headers: set `Content-Type: text/plain; charset=utf-8`
   - Google Sheets API: no action needed (native UTF-8), but avoid pre-encoding strings.
4. **If an emoji is not strictly needed, prefer ASCII-safe alternatives:**
   - ✅ → `[YES]` or remove
   - 🏠 → `[HOUSE]` or remove

## Why
On 2025-06-28 the Oak Park Construction agent crashed with:
`'latin-1' codec can't encode character '\u2705'`
This means an emoji (✅) was passed through a latin-1 encoding path. Enforcing UTF-8 everywhere prevents this entire class of error.
