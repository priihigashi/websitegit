# SKILL: Fix Unicode Encoding Errors (Latin-1 Codec)

## Trigger
Any error matching: `latin-1' codec can't encode character`

## Problem
Somewhere in the pipeline (likely when writing to a Google Sheet, CSV, or making an API call), text is being encoded as `latin-1` instead of `utf-8`. This breaks whenever the generated content contains emoji or non-ASCII characters like ✅, 🏠, 🔨, etc., which are common in social media content for a construction company.

## Rule
1. **All string encoding operations** must use `utf-8`, never `latin-1`.
2. Before writing any generated caption, title, or description to a sheet/file/API, sanitize it:
   - Preferred: ensure the output call uses `encoding='utf-8'`
   - Fallback: strip non-ASCII characters with `text.encode('utf-8', errors='ignore').decode('utf-8')` only if utf-8 output is not possible.
3. **When building HTTP request headers**, if a `Content-Type` header is set, ensure it specifies `charset=utf-8` (e.g., `Content-Type: application/json; charset=utf-8`).
4. **Never silently remove emoji** from social media content — emoji drive engagement. Fix the encoding, don't strip the content.

## How to Verify
After applying, test by including ✅🏠🔨 in a generated post and confirming it writes without error.