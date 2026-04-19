# NONNEGOTIABLES — Oak Park AI Hub

> **Before editing ANY script or workflow:** read this file first.
> Any change that removes or breaks a locked rule requires an explicit note in the commit message.
> Auto-updated nightly by `scripts/nonnegotiables_updater.py`.

_Last updated: 2026-04-19 (seeded from CLAUDE.md)_

---

## CONSUMER MAP — who reads which sections

| Consumer | Reads |
|---|---|
| **carousel_reviewer.py** | Carousel Pipeline, Image Pipeline |
| **content_creator/main.py** | Carousel Pipeline, Image Pipeline, Drive & Storage |
| **approval_handler.py** | Carousel Pipeline, Copy & Brand |
| **4am_agent/main.py** | ALL sections (full compliance check) |
| **nonnegotiables_updater.py** | ALL sections (extracts new rules) |
| **Claude (me, any script edit)** | ALL sections before touching any file |
| **capture_pipeline.py** | Drive & Storage, Script Editing |

---

## HOW TO USE

1. Read this file before touching any script, workflow, or template.
2. If your change affects a locked rule → state that explicitly in the commit message.
3. If a rule becomes obsolete → mark it RETIRED (date) instead of deleting it.
4. New rules are extracted nightly from CLAUDE.md + handoff docs and appended here.

---

## LOCKED — Content & Carousel Pipeline

**MOTION IS DEFAULT ON**
Every carousel build ships BOTH static PNGs AND motion (MP4 + GIF + preview frame).
Motion = OFF only when Priscila explicitly says "static only" for that specific post.
Applies to: scripts, email preview, any manual chat, all skills.
Source: CLAUDE.md — MOTION IS DEFAULT ON

**CAROUSEL FOLDER STANDARD**
Every carousel lands at: `<Series>/_TEMPLATE_CAROUSEL/v<N>_<slug>/` with `png/` + `motion/` siblings.
- `<slug>` = topic only, no date, no post_id prefix
- Version auto-increments on re-build — never overwrites existing versions
- Static and motion are SIBLINGS inside `_TEMPLATE_CAROUSEL`, never nested
Source: CLAUDE.md — CAROUSEL FOLDER STANDARD

**VISUAL-EVERY-OTHER-SLIDE**
Never ship 3+ consecutive text-only slides. At least every other middle slide carries a visual anchor.
News = face for named person OR contextual image (institution, event).
OPC = product shot, tool, material, before-after crop, icon, diagram.
Source: CLAUDE.md — VISUAL-EVERY-OTHER-SLIDE RULE

**NAMED-PERSON FACE RULE**
When content names a person, their face MUST appear on that slide.
Use `.sticker-slot` (cover/hero) or `.bio-card` (multi-person) or `.bio-initials` (no photo available).
Never render a named person's slide without a face treatment.
Source: CLAUDE.md — NAMED-PERSON → FACE RULE

**PER-POST EDITORIAL LOG**
Every post gets a Google Doc log (EP001 — Title — Editorial Log).
Read before touching any carousel. Append dated notes on every feedback.
Source: CLAUDE.md — PER-POST EDITORIAL LOG

---

## LOCKED — Image Pipeline

**IMAGE FALLBACK CHAIN**
For any image slot: CC photo (Wikimedia) first → DALL-E 3 second → `.bio-initials` / placeholder last.
Never ship a post with empty image slots when OPENAI_API_KEY is set.
OPENAI_API_KEY is confirmed set in GitHub secrets since 2026-04-07.
Source: carousel_builder.py + commit 07e76ef (2026-04-19)

**HTML → IMAGE: NEVER SUBSTITUTE AI TOOLS**
`export_slides.js` (Playwright) is the ONLY valid HTML-to-PNG exporter.
Never use OpenAI / Ideogram / Recraft / Seedream / Canva AI / Nano Banana to "convert HTML to image."
Those generate a new design — they do not render the existing one.
Source: CLAUDE.md — HTML → IMAGE

---

## LOCKED — Drive & Storage

**SHARED DRIVE IS DEFAULT**
Files always land in the topic shared drive, never My Drive.
`supportsAllDrives=True` on every Drive API call. Missing = 404 on shared drives.
Topic routing: OPC → Oak Park Construction drive, News → News drive, etc.
Source: CLAUDE.md — DRIVE SHARED DRIVE IS DEFAULT

**DRIVE UPLOAD — BANNED METHODS**
`GOOGLEDOCS_CREATE_FILE` with content = creates empty file.
`mcp__claude_ai_Google_Drive__create_file` with content = creates empty file.
Only valid upload: OAuth Python + `googleapiclient` + `MediaFileUpload` + `supportsAllDrives=True`.
Source: CLAUDE.md — DRIVE UPLOAD BANNED METHODS

**NEWS CONTENT NEVER GOES TO MARKETING DRIVE**
Brazil/USA news → News drive. OPC → OPC drive.
Marketing drive = OPC/general marketing only.
Source: CLAUDE.md + memory feedback_drive_routing_news_vs_marketing.md

---

## LOCKED — Script Editing

**NEVER REWRITE FROM SCRATCH**
Only change what is strictly necessary. Read the full file first.
Extract all IDs, paths, env vars before touching anything.
Show what you are about to change and why BEFORE making any change.
Source: CLAUDE.md — SCRIPT / CODE EDITING RULE

**NEVER GUESS GITHUB SECRET VALUES**
If a script uses `process.env.SOME_ID` from a GitHub secret → STOP and verify.
Never guess which spreadsheet or resource it points to.
Source: CLAUDE.md + memory feedback_never_guess_secret_values.md

**SCRIPTS ADD NEVER DELETE**
New columns go at the end. Resolve by header name, not column index.
Never clear existing cells unless explicitly told to.
Source: memory feedback_scripts_add_never_delete.md

---

## LOCKED — Copy & Brand

**OPC COPY: NO PROMISES**
Never promise what OPC does for clients.
Stats must use ranges (e.g. "$5K–$15K") not exact averages.
Every stat must name its source.
Source: CLAUDE.md + memory feedback_opc_copy_rules.md

**SHADOW BAN & POLITICAL CAPTION RULES**
Attribution-only captions. No party hashtags. Stagger political posts.
Source: memory feedback_shadow_ban_caption_rules.md + CONTENT_FORMATS.md

---

## RETIRED RULES
_(mark here instead of deleting)_

_(none yet)_

---

## PENDING EXTRACTION
_(rules identified in handoffs but not yet verified/formatted)_

- OPC image injection in `_build_opc_html()` — cover image slot not yet implemented (2026-04-19)
- Build Tracker auto-update from all 15 workflows — wired for content_creator only (2026-04-19)
