# NONNEGOTIABLES — Oak Park AI Hub

> **Before editing ANY script or workflow:** read this file first.
> Any change that removes or breaks a locked rule requires an explicit note in the commit message.
> Auto-updated nightly by `scripts/nonnegotiables_updater.py`.

_Last updated: 2026-05-14 (auto-updated by nonnegotiables_updater.py)_

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

**MOTION IS CURRENTLY DISABLED (MOTION_ENABLED=0) — overrides prior "default ON" rule**
Pipeline produces static PNGs only. No MP4, GIF, or reel is built.
Reason: entire motion cascade (Ken Burns/Remotion/Playwright) was broken in 4 ways —
wrong slides animated, text zoomed with background, wrong frames on cover, wrong tool entirely.
Do NOT re-enable until a new motion plan is designed and approved by Priscila.
To re-enable: set MOTION_ENABLED=1 in GitHub secrets AND get explicit approval.
Updated: 2026-05-13. See commit bf1d7c1.

**CAROUSEL FOLDER STANDARD**
Every carousel lands at: `<Series>/_TEMPLATE_CAROUSEL/v<N>_<slug>/` with `png/` + `motion/` siblings.
- `<slug>` = topic only, no date, no post_id prefix
- Version auto-increments on re-build — never overwrites existing versions
- Static and motion are SIBLINGS inside `_TEMPLATE_CAROUSEL`, never nested
Source: CLAUDE.md — CAROUSEL FOLDER STANDARD

**MOTION RENDERER CASCADE (added 2026-04-20, updated 2026-04-29)**
Main pipeline renders in this order:
1. Remotion (`scripts/remotion/src/CarouselMotion.tsx`, composition id `CarouselMotion`) — used when `cover_renderer_pref == "remotion"`.
2. Playwright `record_motion.js` — records per-slide motion HTMLs built by `build_motion_html()`. Each HTML has CSS Ken Burns on `.kb-bg` (background layer only — text is z-index 2, stays static) + optional clip sticker (looping `<video>` in `.clip-frame`). This is the PRIMARY motion renderer for all HTML-source templates.
3. Alert — if motion/ folder is empty after Remotion + Playwright, pipeline alerts and skips the post. Motion is never silently absent.

ffmpeg Ken Burns zoompan on full PNG is ONLY used by `run_motion_only()` (manual_template=motion) and writes to `motion_remotion/` subfolder for comparison — it is NOT part of the automatic new-build flow.
Never substitute an AI video tool (Kling / Runway / Pika) unless Priscila explicitly approves per post.
Source: CLAUDE.md — MOTION RENDERER CASCADE + scripts/content_creator/MOTION_SOURCES_RESEARCH.md

**VIDEO SOURCE CASCADE — 8 TIERS (added 2026-04-20)**
For any slide that needs a live clip (speech / event / institutional b-roll), `motion_sources.fetch_clip_with_fallback` tries 8 sources before Ken Burns:
YouTube (Apify) → Instagram (Apify) → Pexels → Pixabay → Archive.org → Wikimedia Commons → Stock scrapers → Ken Burns floor.
- Haiku must emit DIFFERENT query phrasing per tier (`youtube_query` ≠ `pexels_query` ≠ `pixabay_query`).
- Every successful fetch writes `<clip>.source.txt` sidecar with tier, url, license, attribution, query, fetched_at.
- Stock tiers (Pexels/Pixabay/Archive/Wikimedia) skip for `visual_hint == "bio-card"` — faces never come from generic stock.
- If every tier fails, Ken Burns animates the poster. Never empty motion folder.
Source: scripts/content_creator/motion_sources.py + MOTION_SOURCES_RESEARCH.md

**BRAZIL NATIVE TEMPLATE — V1 RACHADINHA MOTION TREATMENT (added 2026-04-29)**
Every Brazil native carousel uses the Rachadinha v1 visual system. Applies to all agents (carousel_reviewer, content_creator, 4AM agent, any skill that builds or reviews Brazil carousels):
- Cover slide: full-bleed CC photo as `.bg-photo` with `filter:grayscale(1) contrast(1.1) brightness(.55)` + `.halftone` dot overlay + `.sticker-slot` portrait (absolute right 7% top 18%, same photo, `filter:grayscale(1) contrast(1.15) brightness(.95)`). Cover text constrained to max-width 54% to avoid collision.
- Middle slides: ODD indices (3, 5, 7…) = motion slide with `.bg-photo` + `.halftone`. EVEN indices (2, 4, 6…) = static, no background.
- Photo source: `photo_query` field in `clip_suggestions` → Wikipedia REST → Wikimedia Commons → Pexels fallback. Haiku MUST emit `photo_query` + `photo_bg_position` for every motion slide.
- `motion_renderer` must be `"kenburns"` for Brazil native — Playwright records the CSS Ken Burns animation on the `.kb-bg` background layer only (text/logo stay perfectly static via z-index). This is CSS `@keyframes kb-zoom` on the div behind the text, NOT ffmpeg zoompan on the full rendered PNG. ffmpeg on a full PNG moves text too — that is wrong.
- NO placeholder divs ever. If no photo fetched → slide renders as clean dark text, no dashed box.
Source: v1_rachadinha/cover.html (Drive 1TgH7nDM2BDFznL9jS9jmzCdt9XNT3y0y) + carousel_builder.py::_build_brazil_html

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

## LOCKED — Workflow & Tracking

**SPREADSHEET HUB — LOG EVERY TAB**
Any new spreadsheet created OR any new tab added to an existing spreadsheet → immediately add a row to the Hub.
Hub ID: 1qDbO6JQX0cKbZ9rHjiM7a4U_p7OOddZ3k3Sp30JJoqo. Columns: SPREADSHEET | TAB | PURPOSE | LINK | SPREADSHEET ID.
Never skip this step. One row per tab.
Source: CLAUDE.md — SPREADSHEET HUB

**FLOW PLANS TRACKER — LOG EVERY NEW DOC**
Every new flow doc, master plan, process doc, or niche strategy created → add a row immediately.
Tracker ID: 1fggy918FgPfnMQ-dzGQk2zx9uhi2_-uWXMKGW4MA47k. Tab: All Docs. 4AM agent reads this as its plan manifest.
Columns: NAME | TYPE | NICHE | STATUS | DESCRIPTION | OPEN | DOC_ID | TABS | LAST UPDATED.
Source: CLAUDE.md — FLOW PLANS TRACKER

**CAPTURE OUTPUT MINIMUM**
Every video capture must produce: 1 carousel idea (angle + slide structure outline) + 1 reel idea (hook + format) + 2-3 topic breakdowns. Never just download a clip.
Topics = raw material. Posts = original. Never use captured person's clip unless explicitly asked.
Source: CLAUDE.md — CAPTURE

**CONTENT FORMATS REGISTRY**
Read CONTENT_FORMATS.md (Drive ID: 1XqXSyJC_iHMTrmMxpM5ZR7S-WQxz19HhDJO1HomdncM) before producing any content (carousel, reel, hooks, copy).
Never create a format that already exists. Write to it whenever Priscila names a new format.
Current formats: FORMAT-001 (Split Screen+Sources Below), FORMAT-002 (Quem decidiu isso?).
Source: CLAUDE.md — CONTENT FORMATS

**SESSION EXIT — 3 MANDATORY STEPS**
Every session exit (exit/closing/done for today) must complete all 3:
1. Chat log → Chat Logs folder (Drive ID: 1qitnbz5_8tfZI2rnTogV1zLLLLOwFVCw)
2. Productivity & Routine doc (ID: 1wVBuNOuOufT8WP4KCrrlVbKWRmQZjKvqmia1soUEBZE) — mark done, add new tasks
3. Handoff doc → Productivity & Routine folder (ID: 1b8Cfc8lJhu5unDaxDQIdo4xdN6X7n1nS) if context near limit
All 3. Every exit. No exceptions.
Source: CLAUDE.md — SESSION EXIT LOG

---

## RETIRED RULES
_(mark here instead of deleting)_


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-04-19)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-04-19)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-04-19)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-04-19)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-04-19)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


---

## LOCKED — Pipeline Resilience

**3-ROUTE MINIMUM FOR EXTERNAL SERVICE CALLS (added 2026-04-19)**
Any external API call in a pipeline script must have at least 3 fallback routes coded in.
Order: most reliable / most accurate first → cheaper/simpler fallback → offline/placeholder last.
Never let a single failed API kill the whole pipeline step.
Current implementation: image generation (CC photo → DALL-E 3 → bio-initials). Verify and apply to: Drive upload, email send, scraping, video render.
Source: Priscila request 2026-04-19 — "the pipeline may need more than one two options... we need to have everything at least three options"

**PIPELINE REFERENCE DOC IS THE SOURCE OF TRUTH (added 2026-04-19)**
Before asking "what does X script do" or "which folder does Y go to" — read the pipeline doc first.
Doc: https://docs.google.com/document/d/1XGmbnvyS_WomKl3USVFz-pPg-3agTn5Bl0QpyMbeHs4/edit
Covers: all scripts, triggers, I/O, folder IDs, spreadsheet IDs, credentials, failure playbook, manual gaps.
Built from live script audit 2026-04-19. Update it when scripts change.

---

## PENDING EXTRACTION
_(rules identified in handoffs but not yet verified/formatted)_

- Build Tracker auto-update from all 15 workflows — wired for content_creator only (2026-04-19)
- 3-route fallback not yet verified in code for: Drive upload, email send, scraping — audit each script (2026-04-19)

## RESOLVED EXTRACTIONS
_(items confirmed implemented — moved from PENDING)_

- OPC cover image slot — IMPLEMENTED. `_build_opc_html()` line 685: `cover_img = (media_paths or {}).get("cover","")` renders `bg_photo_el`. `.sticker-slot` with "ON-SITE · SWAP-IN" is intentional placeholder for real project photos, NOT an unresolved image slot. (resolved 2026-04-19)
- USA The Chain _TEMPLATE_CAROUSEL ID — FIXED. main.py line 39 now `1sDMyPHVYcOqZ3NK9ch4e48AaJ7KVvxL3`. Democrat folder + shortcuts moved to USA The Chain. (resolved 2026-04-19)


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-04-20)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-04-20)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-04-20)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-04-20)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-04-20)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-04-21)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-04-21)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-04-21)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-04-21)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-04-21)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-04-22)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-04-22)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-04-22)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-04-22)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-04-22)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-04-23)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-04-23)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-04-23)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-04-23)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-04-23)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-04-24)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-04-24)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-04-24)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-04-24)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-04-24)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-04-25)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-04-25)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-04-25)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-04-25)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-04-25)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-04-26)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-04-26)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-04-26)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-04-26)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-04-26)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-04-27)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-04-27)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-04-27)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-04-27)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-04-27)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-04-28)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-04-28)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-04-28)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-04-28)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-04-28)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-04-29)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-04-29)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-04-29)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-04-29)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-04-29)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-04-30)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-04-30)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-04-30)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-04-30)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-04-30)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-05-01)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-05-01)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-05-01)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-05-01)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-05-01)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-05-02)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-05-02)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-05-02)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-05-02)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-05-02)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-05-03)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-05-03)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-05-03)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-05-03)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-05-03)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


═══════════════════════════════════════════════════════════════════════
SELF-HEAL BOT RULES — appended 2026-05-03
═══════════════════════════════════════════════════════════════════════

These rules apply to the pipeline_self_heal.yml bot AND to any human or
AI session that touches the repo. They are non-negotiable. The bot
reads this file at the start of every cycle and refuses any action
that would violate them.

NN-S1 — APPEND-ONLY DOCTRINE FOR DOCS
  - Google Docs are NEVER overwritten. New information ALWAYS goes at
    the bottom in a new dated block: "NOTE — YYYY-MM-DD HH:MM UTC".
  - Prior NOTE blocks are NEVER edited or deleted. If a prior decision
    was wrong, write a new NOTE that supersedes it; the old text stays.
  - This applies to: HANDOFF docs, story docs (EP001 format), Fixing Log
    Docs, master plan docs, _CHECKLIST.md, every CAROUSEL_FOLDER_STANDARD
    story doc, this file (NONNEGOTIABLES.md).

NN-S2 — APPEND-ONLY DOCTRINE FOR SCRIPTS / WORKFLOWS
  - Working scripts are NEVER deleted or rewritten from scratch.
  - Only the section that needs to change may be edited. If a fix would
    require deleting a working function, the bot REFUSES the patch and
    marks the task NEEDS-REVIEW.
  - This was historically violated when "fixing storytelling" caused
    internal label leakage into carousels. The new rule prevents that
    class of bug.

NN-S3 — BACKUP BEFORE ANY ALTER
  - Before editing ANY file (script, workflow, doc, sheet schema), the
    bot copies the current version to:
      - Drive: /PIPELINE FIX/Backups/  (for repo files)
      - Drive: copy_file API           (for Google Docs)
  - Backup filename: BACKUP_<task_id>_<original_path>_<utc-iso>
  - Backup confirmation must appear in the Fixing Log Doc before the
    bot proceeds with the edit.
  - Git tag self-heal-baseline-2026-05-03 is the absolute rollback
    anchor for the repo. Never delete or move that tag.

NN-S4 — NO SILENT CARROUSEL LABEL LEAKAGE
  - If carousel slide text contains any of: "one should say", "the
    narrator says", "[INSERT", "{{", "TODO:", "PLACEHOLDER", "XXX",
    "Slide N:", "Hook:", "CTA:", "Body:" — that is a build failure.
    The bot must mark the build red and the carousel must NOT ship.
  - This rule was added because a prior storytelling-fix attempt
    introduced internal labels into the rendered output. SH-020
    (carousel storytelling guardrail) implements the detector.

NN-S5 — READ THIS FILE BEFORE EVERY CYCLE
  - The self-heal orchestrator reads NONNEGOTIABLES.md as the FIRST
    action of every cycle (before reading the queue, before any patch).
  - If a proposed patch would violate any NN-* rule, the patch is
    rejected and the task is marked NEEDS-REVIEW with the rule ID.
  - Any human session continuing this work must also re-read this file
    before starting (per CLAUDE.md SCRIPT / CODE EDITING RULE — already
    in effect, restated here for emphasis).

NN-S6 — NEW NON-NEGOTIABLES APPEND HERE
  - Any new non-negotiable discovered during work goes at the BOTTOM
    of this file with a date-stamped header. Never insert in the middle.
    Never delete an old rule even if it is superseded. Mark superseded
    rules as such in a follow-up NOTE; do not erase them.

═══════════════════════════════════════════════════════════════════════
END OF 2026-05-03 APPEND
═══════════════════════════════════════════════════════════════════════


═══════════════════════════════════════════════════════════════════════
SELF-HEAL BOT RULES — appended 2026-05-03 (NN-S7 wiring audit)
═══════════════════════════════════════════════════════════════════════

NN-S7 — WIRING AUDIT BEFORE A FLOW IS "DEPLOYED"
  Background: prior failures came from product-to-key mismatches (e.g. a
  NanoBanana flow referencing OPENAI_API_KEY, secret names that did not
  match what the workflow expected, "fixes in chat" that never reached
  the actual wiring). To prevent recurrence:

  Every flow (workflow file + script) MUST register a row in the
  Wiring Audit sheet tab BEFORE it is considered deployed. Required
  columns:
    - workflow_file:       which .yml uses this
    - step_name:           which step calls the API
    - tool_or_api_used:    e.g. "Anthropic Claude API", "NanoBanana 2"
    - required_key_name:   the conceptual name (e.g. "Anthropic key")
    - secret_name_in_repo: the exact GitHub Actions secret name
    - secret_exists:       Y/N (verified against repo Settings)
    - last_successful_run: timestamp of last green run for this flow
    - cascade_fallback:    name of the fallback path if this fails

  Enforcement:
  - The self-heal bot REFUSES to mark any new SH-* task as "DONE"
    until the new flow has its row in the Wiring Audit tab with
    secret_exists=Y.
  - A nightly cron audits all rows. Any row where secret_exists=N OR
    last_successful_run > 7 days ago triggers an email to Priscila
    listing the broken flows, the keys involved, and where to fix.

NN-S8 — CASCADE-OR-EXPLICIT-NOPE
  Every external-dependency flow must EITHER document a cascade of at
  least 2 alternatives, OR explicitly note "no cascade available" with
  a written reason. Flows without either are considered NEEDS-REVIEW.

  Examples (correct as of 2026-05-03):
  - Patch generation:   Claude → OpenAI → Gemini-if-key → NEEDS-CREDITS
  - Image gen:          NanoBanana → Replicate → Pexels → CSS-card fallback
  - Posting:            Buffer (no cascade — Buffer IS the cascade
                        because it fans out to multiple platforms)
  - Scraping:           YouTube Data API → Apify Instagram → Apify YouTube
  - Translation:        Claude Haiku → OpenAI → Google Translate

NN-S9 — DO NOT ADD UNVERIFIED PRODUCTS / APIS
  When implementing a new flow, do NOT assume an API exists or works.
  Verify FIRST that:
  (a) the API endpoint resolves with the expected key,
  (b) the documented features are actually available on the user's
      account tier,
  (c) the secret is wired and the workflow can read it.
  If any of (a)(b)(c) fails, the flow is documented as "blocked on
  external dependency" — never silently skipped.

  Specific reminders for current planning:
  - TikTok Content Posting API requires manual developer approval
    that Priscila has not yet completed. Do NOT add TikTok publishing
    to any flow until that approval lands.
  - NanoBanana 2 uses its own API key — NOT the OpenAI key. Confirm
    the secret name before any flow references it.
  - Buffer is the live posting layer. Do not bypass Buffer with
    direct platform API calls unless Priscila explicitly requests it.

═══════════════════════════════════════════════════════════════════════
END OF 2026-05-03 NN-S7..S9 APPEND
═══════════════════════════════════════════════════════════════════════


═══════════════════════════════════════════════════════════════════════
SELF-HEAL BOT RULES — appended 2026-05-03 (NN-S10 image cascade lock)
═══════════════════════════════════════════════════════════════════════

NN-S10 — IMAGE CASCADE ORDER IS LOCKED FOR OPC NICHE
  Background: code-level audit on 2026-05-03 found carousel_builder.py
  was running AI image generation BEFORE real photos, contradicting
  IMAGE_QUALITY_RULES.md and producing hallucinated/cartoonish images
  for realistic construction content. Fixed via SH-033/SH-038/SH-039.

  Locked cascade order (OPC niche only):
    1. photo_matcher.match_opc_photo()  — real OPC project photos from
                                          the 📸 Photo Catalog tab
    2. image_library                    — previously enhanced reuse
    3. fetch_wikimedia()                — CC photos (institutions, events)
    4. fetch_pexels()                   — royalty-free stock
    5. fetch_pixabay()                  — royalty-free stock backup
    6. NB2 / Seedream4.5 / Seedream5.0 / Gemini / SDXL  — AI fallback
                                          (flag fix_type=regenerate)
    7. NO DALL-E for OPC                — produces cartoonish output;
                                          if all of (1)-(6) fail, return
                                          empty path and use typographic
                                          slide template instead.

  News/Brazil niche keeps AI-first per the existing comment in
  carousel_builder.py L1596-1599 (real-photo search returns generic
  stock for political topics). DALL-E is allowed there.

  Vision validator gates EVERY tier. _vision_accept must reject:
    - hallucination markers (extra fingers, garbled text in image,
      impossible geometry, crowd in solo-tip context)
    - wrong subject (query says kitchen, image shows bathroom)
    - cartoonish style on realistic-content niches
    - watermarks
    - landscape orientation in portrait slot

  Any patch that reverses cascade order (puts AI before real photos for
  OPC) MUST be rejected by the self-heal guard. Add tier_order_lock
  to patch_violates_nonnegotiables() in orchestrator.py.

NN-S11 — TEXT NEVER CROPS, ALWAYS RESIZES
  Background: opc_tip_base.css uses fixed font-size:128px on headline
  inside a 1080x1350 container with overflow:hidden. Long headlines
  get clipped at the bottom edge instead of resizing.

  Required behavior (all slide CSS):
    - Every text-bearing CSS rule uses font-size: clamp(min, vw, max)
      OR is paired with a JS auto-shrink loop in export_slides.js.
    - Container overflow:hidden is ALLOWED only when JS auto-shrink
      runs first.
    - Outer safe-zone margin >= 60px on all 4 sides of every slide
      (currently inconsistent; cover has 108px L/R but other slides
      touch the edges).
    - line-height >= 0.95 on display fonts, >= 1.3 on body copy.

  Any patch that adds a fixed font-size > 24px without clamp() and
  without a corresponding auto-shrink hook is rejected by the
  self-heal guard.

═══════════════════════════════════════════════════════════════════════
END OF 2026-05-03 NN-S10/S11 APPEND
═══════════════════════════════════════════════════════════════════════


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-05-04)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-05-04)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-05-04)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-05-04)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-05-04)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-05-05)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-05-05)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-05-05)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-05-05)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-05-05)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-05-06)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-05-06)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-05-06)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-05-06)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-05-06)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-05-07)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-05-07)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-05-07)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-05-07)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-05-07)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-05-08)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-05-08)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-05-08)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-05-08)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-05-08)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-05-09)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-05-09)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-05-09)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-05-09)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-05-09)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-05-10)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-05-10)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-05-10)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-05-10)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-05-10)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-05-11)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-05-11)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-05-11)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-05-11)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-05-11)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-05-12)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-05-12)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-05-12)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-05-12)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-05-12)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p


- **CREATING CALENDAR EVENTS — try in order, never give up:** (from repo CLAUDE.md, 2026-05-13)
  ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred) ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(


- **DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE** (from repo CLAUDE.md, 2026-05-13)
  ROUTING BY PROJECT — always check which drive before creating anything: - Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Clau


- **EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopp** (from repo CLAUDE.md, 2026-05-13)
  Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions. - ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send - ROUTE


- **AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"** (from repo CLAUDE.md, 2026-05-13)
  Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents: - /AIOX-architect — system design, API routing, architecture decisions - /AIOX-devops — GitHub Actions wo


- **SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE** (from repo CLAUDE.md, 2026-05-13)
  Never rewrite a working script from scratch. Only change what is strictly necessary. Before any edit: read the full file, list what you're changing and why. Good things already in the script must be p



═══════════════════════════════════════════════════════════════════════
MOTION SYSTEM V2 SPEC — updated 2026-05-14 (approved by Priscila)
═══════════════════════════════════════════════════════════════════════

NN-M1 — MOTION SOURCE CASCADE (3 TIERS, KEN BURNS PERMANENTLY REMOVED)

  Tier 1: Real short clip (~5 sec, looped cleanly)
    Use when slide topic has: named person, place, room, city, object,
    institution, project, or source clip. This is the primary path.

  Tier 2: GIPHY
    Fallback for generic expressive/ambient motion when no real footage
    is available AND the topic is safe for that tone.
    NOT for serious person-evidence or news proof slides unless it is
    clearly just an accent layer.

  Tier 3: Static image or designed visual motion
    Last resort only. No Ken Burns — ever. Ken Burns is permanently removed,
    not deferred. If no clip and no safe GIPHY match: deliver static PNG,
    no motion slot. No empty MP4.

NN-M2 — MOTION LAYOUT ROUTES (4 TYPES — C IS PHASE 2+)

  A. Sticker Portrait
     Small to medium vertical video sticker (person/object card).
     Best for: named people, products, officials, contractors, tools,
     material samples.
     Position: framed sticker, typically top-right or inset.

  B. Accent Window
     Medium rectangle (landscape or portrait), placed like an image slot.
     Best for: slides with moderate text that need a visual anchor.

  C. Network / Multi-Face Motion  [PHASE 2 — DO NOT IMPLEMENT IN PHASE 1]
     Mostly static layout with 1 animated main person/object and surrounding
     static connected faces/items. Design routing hooks now but
     do NOT implement until Phase 2.

  D. Full Background Clip
     5-second looping background video + dark overlay + static title on top.
     Use for: place, institution, city, Congress, house timelapse, jobsite,
     room, atmosphere.
     NOT for every post — only when the router labels it as strongly fitting.

NN-M3 — ROUTING LOGIC (PER-SLIDE HINT OVERRIDES ROUTE SET)

  Routing by slide type:
    Person named              → prefer A
    Object/material/tool/product → A or B
    Place/institution/city/room/house → B or D
    Light text cover + strong visual topic → D allowed
    Heavy text slide          → smaller A/B only, or static
    Proof/source slide        → usually static unless the proof IS a clip
    Many connected people     → C (Phase 2 only)
    No relevant clip          → static PNG only, not fake motion

  Per-slide routing_hint (layout_hint, subject_type, text_density) from
  carousel_builder.py takes precedence over any global route-set rotation.
  Route sets are the default; per-slide hint wins.

NN-M4 — SEQUENCE VARIETY — ROUTE SETS (ROTATION, NOT OVERRIDE)

  Rotate through these sets so posts do not feel repetitive.
  Per-slide routing (NN-M3) still overrides when content does not fit.

    Route Set 1: cover A   · slide 3 B         · slide 5 static
    Route Set 2: cover D   · slide 3 A         · slide 5 B
    Route Set 3: cover static · slide 2 A      · slide 4 B
    Route Set 4: cover B   · slide 3 C (later) · slide 5 static

NN-M5 — PHASE 1 SCOPE: COVER A + COVER D, PLAYWRIGHT ONLY, MANUAL ONLY

  Phase 1 tests ONLY these two routes:
    Cover A: static cover + framed sticker video (small/medium portrait clip)
    Cover D: static text overlay + full background clip with dark overlay

  NOT in Phase 1:
    - Middle slides with motion
    - Ken Burns (permanently removed, not deferred — never bring it back)
    - Kling (not in Phase 1)
    - Text movement of any kind
    - CarouselMotion.tsx / Remotion (deferred to Phase 2)
    - cron/prod motion defaults (MOTION_ENABLED stays 0 until Priscila approves)
    - Buffer approval logic changes
    - Route Set C (multi-face) — Phase 2 only

  PROOF TESTS REQUIRED BEFORE EXPANSION:
    1. Cover A: framed clip sticker renders at correct position, text static
    2. Cover D: full-bleed clip, readable dark overlay, text static
    3. No-clip test: no clip found → static PNG only, no empty MP4
    4. Cover-only: only the cover PNG gets a motion version produced
    5. Layout D text: static title legible over full-bleed clip
  Drive links to test output are required. "I ran it" is not sufficient.
  Priscila must visually compare A vs D and choose before any expansion.

  CarouselMotion.tsx (Remotion) is FROZEN until Phase 2.
  Any PR touching CarouselMotion.tsx during Phase 1 is rejected.

═══════════════════════════════════════════════════════════════════════
END OF MOTION SYSTEM V2 SPEC — updated 2026-05-14
═══════════════════════════════════════════════════════════════════════
