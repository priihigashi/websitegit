# SKILL: pipeline-fix
# Reads at session start. Report status immediately. No re-explaining needed.

---

## SESSION START — DO THIS FIRST (sync-up protocol)

Step 1 — Read the PIPELINE INVENTORY doc (link in Section 1) to get the most current state. It may be more up to date than this file.
Step 2 — Read sections 3 and 4 of this skill.
Step 3 — Check GitHub for commits since last session: `gh run list --repo priihigashi/oak-park-ai-hub --limit 5`
Step 4 — Say exactly: "Pipeline-fix loaded. Pipeline status: [building/broken]. Top pending fix: [one item]. Starting now."
Step 5 — Execute the top item in section 4 PENDING list. Do not ask. Do not explain. Fix it.

WHAT TO CARRY FORWARD (find and continue):
- Any fix from section 4 PENDING that has a file path and clear description — start coding it
- Any template not yet walked through (section 4 IN PROGRESS) — continue the walkthrough
- Any test run result from last session — review it and react

---

## 1. MASTER REFERENCE INDEX

### Gallery (template viewer)
- Page 1 Clean:   https://priihigashi.github.io/oak-park-ai-hub/docs/templates/index.html
- Page 2 All:     https://priihigashi.github.io/oak-park-ai-hub/docs/templates/wired.html
- Page 3 Chosen:  https://priihigashi.github.io/oak-park-ai-hub/docs/templates/selected.html
- Local files:    docs/templates/*.html (in oak-park-ai-hub repo)

### GitHub
- Repo:           priihigashi/oak-park-ai-hub
- Workflows:      .github/workflows/ — content_creator.yml, capture_pipeline.yml, capture_queue.yml, send_email.yml, video-research.yml, 4am_agent.yml, drive_route_file.yml

### Key Scripts
- Carousel builder:   scripts/content_creator/carousel_builder.py
- Pipeline runner:    scripts/content_creator/main.py
- Email preview:      scripts/content_creator/email_preview.py
- Motion sources:     scripts/content_creator/motion_sources.py
- 4AM scraper:        scripts/4am_agent/scraper.py
- Capture pipeline:   scripts/capture/capture_pipeline.py
- HTML→PNG export:    /Users/priscilahigashi/ClaudeWorkspace/Content Templates/_Scripts/export_slides.js

### Planning Docs
- Full Circle Plan:   https://docs.google.com/document/d/17B8wc4wWmcBapl_4gduHyMzjYl4R4y_RYuvZae5xRPU/edit
- Flow Plans Tracker: https://docs.google.com/spreadsheets/d/1fggy918FgPfnMQ-dzGQk2zx9uhi2_-uWXMKGW4MA47k

### Drive — PIPELINE FIX Folder (source of truth for fix history)
- PIPELINE FIX folder:               https://drive.google.com/drive/folders/1FHPkx8VA6c-Wmy6hI3uX_weSPwJPBp3z
- HTML TEMPLATES subfolder:          https://drive.google.com/drive/folders/1PMwIYurzv2GY077hlz7oHr82hNPsY6XI
  - OPC subfolder:                   https://drive.google.com/drive/folders/1F5P8RkpvwdyR2DLXEXKJPmKIte6G8SUr
  - NEWS subfolder:                  https://drive.google.com/drive/folders/1UiIjuHbI3kdFlmaiCalNWAn5leV7Jc3H
- FULL CIRCLE PLAN (master plan):    https://docs.google.com/document/d/17B8wc4wWmcBapl_4gduHyMzjYl4R4y_RYuvZae5xRPU/edit  ← START HERE
- Template Registry (series×template): https://docs.google.com/document/d/17HnoR76_D-L1KOj8Qng03zIHZGwwsk2R9JQ5eKzuEEI/edit
- Carousel Design Feedback Log:      https://docs.google.com/document/d/1zonzZNmW5wdDmtJxzozyqaBpf1i6KX068vEyhJ2qb_4/edit  (APPEND ONLY — change log)
- Gallery quick-link doc:            https://docs.google.com/document/d/1xHU5NQZMYtDIVM93LIlZUDkz0biBKTxvQzaMaPnEows/edit
- PIPELINE INVENTORY (fix history):  https://docs.google.com/document/d/1yPsSqh24ioXU3cwwnkwH0oeSUsenTDvqESrIXfJ066A/edit
- Fix Report — OPC full audit:       https://docs.google.com/document/d/1d7AxdhpP6C93iOlM0Wwn1e93TIuQa3HukeliLjYcLjE/edit
- Fix Report — general pipeline:     https://docs.google.com/document/d/1J-jn4NrQXu_if8T9_Bz4wyzZ-zQqx7gE33CbiQ-KcTw/edit
- Fix Report — Dados vs Opinião:     https://docs.google.com/document/d/1TUzpI_zeOG0Vndc7vZ5wu7ZCM0E2Tj5kbiZ9bJCvUrU/edit
- Fix Report — FORMAT-019:           https://docs.google.com/document/d/1pxnNTGaQwIj7l7XepDCt_6LalxlYOyHhK6O6_ItTsjs/edit

### Repo files added in fix sessions (not just scripts/content_creator/)
- PIPELINE_REGISTRY.md:     repo root — template source of truth
- PIPELINE_FIX_SEQUENCE.md: repo root — per-session checklist
- scripts/content_creator/carousel_reviewer.py
- scripts/content_creator/topic_picker.py
- scripts/content_creator/photo_matcher.py

### Last test carousels (Drive folders — for visual QA reference)
- OPC v11 "How to Choose the Right Contractor": static → 12meJqAecsOCaFVDGJ1L8WkOH7BtUKLJP | motion → 1LhEYq-acnlADHvQqmX0_1jaZGkdoc7bx
- Brazil Dados v3 (Gastos Judiciário): static → 1ZGfBWsPvkDKWnoGKCQXpQPPyaV7-1BoC | motion → 1bOAUsPGWQx_91rzwdl62873ReQHGXarg
- OPC scheduled run x3 (08:27 UTC 2026-05-01): driveway-sinkhole, satisfying-epoxy, diy-garden-arbor folders in OPC _TEMPLATE_CAROUSEL

### Spreadsheets
- Ideas & Inbox:      1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU  (Inspiration Library, Pipeline Failures tabs)
- Content Sheet:      1C1CAZ8lSgeVLSSCYIg-D9XPJcSLHyIOh1okKtvhZZQg
- Spreadsheet Hub:    1qDbO6JQX0cKbZ9rHjiM7a4U_p7OOddZ3k3Sp30JJoqo

### Drive — _TEMPLATE_CAROUSEL Folder IDs (locked anchors)
- OPC Tip of the Week:            1PWrZfuOvyHUbTRlFNqYxdhtg-Zvv_bXb  (Marketing drive)
- Brazil Quem Decidiu Isso:       1Ts4OlXT_KxtYNziGmHUcsjHVh8Z7D1ds  (News drive)
- Brazil Verificamos:             1QhILiMiIM9WrpHhIqXXrPs6JqoAdDijA  (series: 1IPLdQeTzGnWwN9MZKvfJSOXvSyK4xI5p)
- Brazil A Conta que Ninguém Pagou: 1AwdqHecqyjGAOwPsjrYuO_NajMxLUkWH (series: 1gaLG4ObKuMx1qOb-8r63XqaKXmfRdtLI)
- Brazil Arquivo Aberto:          163TWpEIGxkPuh86eCBKMIraz83YHHEzR  (series: 1lvDlx4jn0fNbdJJQx9NAKG56I05ePqR2)
- USA The History They Left Out:  15GxuxNyZco9W9GL2CZXoeiArIp1l4I9d  (series: 1ZDuaLyvFYLLGoOVQlOBRsOxa-WVGwJ5g)
- USA The Chain:                  1sDMyPHVYcOqZ3NK9ch4e48AaJ7KVvxL3  (series: 15hYZoMVFA0u9vZR0SvZQ3z7SkSbqBEYf; EP001: 1CjaNjewnNUKd3NvMmcVpET7IKF6Mk3_Z)

### Drive — Carousel Parent Folders
- OPC:    16P2JN74JAAW3HKnmNqPGPrAq7N5jDNii  (Marketing/Content/carousel)
- Brazil: 1gDOjtW_X-_jWtu94pffbDaUsw6VGCKuA  (News/Brazil/Carousel)
- USA:    1lRfZE5XC_gL57pUiiWu0Lhar9wfyCtFw  (News/USA/Carousel)

### Drive — Other
- Chat Logs folder: 1qitnbz5_8tfZI2rnTogV1zLLLLOwFVCw
- Productivity & Routine doc: 1wVBuNOuOufT8WP4KCrrlVbKWRmQZjKvqmia1soUEBZE

---

## 2. FULL CIRCLE PLAN — EXECUTION ORDER (LOCKED)

Workflow: Templates → Wire → Run → React (Writing OR Images)
Source doc: https://docs.google.com/document/d/17B8wc4wWmcBapl_4gduHyMzjYl4R4y_RYuvZae5xRPU/edit

SECTION 1 — Template Selection (WHERE WE ARE NOW)
  Walk through all templates. Lock category + sub-series. Build selected.html.
  Order: OPC first → Brazil → Brazil Contexto → USA
  OPC Tip of the Week ✅ locked. Next: opc_progress → opc_illustrated → opc_cutout → News

SECTION 2 — Template Design Gaps (done per template, right after it's locked)
  P1 Face slot (news_brazil_shared + news_usa_shared) — unblocks 5 series
  P2 .compare-grid — unblocks Dados vs Opiniao
  P3 Verdict slide — unblocks fact-check series
  P4 Before/after panel — unblocks opc_progress
  P5-P10 — context slots, avatar, map, data viz, 6-slide layout

SECTION 3 — Wire to Pipeline (carousel_builder.py + naming consistency + schema + Drive folder map)
SECTION 4 — Run Pipeline (trigger content_creator.yml, observe preview email)
SECTION 5 — Writing fixes (ONLY if Section 4 text is bad)
SECTION 6 — Images/Video fixes (ONLY if Section 4 visuals are bad)
SECTION 7 — Hygiene (topic_picker bugs, APPROVE button placement, queue cleanup)

KEY RULE: No content built in chat. Everything through the pipeline. React to email output.

---

## 3. PIPELINE ARCHITECTURE (end-to-end)

1. CAPTURE — URL dropped into Inspiration Library (Ideas & Inbox). capture_pipeline.yml runs, downloads video, writes transcript, saves to Content Hub in Drive.
2. TEMPLATE — carousel_builder.py generates HTML from a locked series template (see selected.html for chosen templates). Template defines slide structure, color palette, and image slot positions.
3. IMAGE EXPORT — export_slides.js (Playwright) renders HTML → PNG per slide. Output: png/ subfolder in /tmp/<run-id>/<slug>/
4. MOTION — motion_sources.py fetches video clips (8-tier cascade). ffmpeg / Remotion renders cover MP4 + GIF. Output: motion/ subfolder.
5. DRIVE UPLOAD — main.py uploads png/ + motion/ to the locked _TEMPLATE_CAROUSEL folder. Uses supportsAllDrives=true. Version auto-increments (v1 → v2 → ...).
6. EMAIL PREVIEW — email_preview.py sends one email per carousel with full slide stack + reply commands (APPROVE / NOT GOOD / REJECT).
7. APPROVAL — approval_handler.py reads Gmail reply. APPROVE → schedules to Buffer. NOT GOOD → logs feedback + waits for rebuild.
8. POST — Buffer schedules and posts to Instagram.

Trigger: content_creator.yml runs at 2:30 AM daily. Manual trigger via GitHub Actions UI or gh CLI.

---

## 3. CURRENT STATUS SNAPSHOT
[Updated: 2026-05-01 — corrected from Pipeline Inventory doc]

PIPELINE IS BUILDING ✅ — carousels render, reviewer passes, Drive upload works, emails send.
Remaining issues are QUALITY problems, not infrastructure failures.

WORKING ✅
- Pipeline runs end-to-end: topic_picker → carousel_builder → PNG render → Drive upload → email preview
- Reviewer (carousel_reviewer.py) — passes 1/1 on OPC and Brazil
- Caption generation — generate_caption() added to main.py, caption.txt saved to Drive folder ✅
- Email preview — niche label dynamic (Brazil emails say [BRAZIL], not [OPC]) ✅
- Stat overflow — clamp() + overflow:hidden in CSS ✅
- SWIPE arrow overlap — padding-bottom 80px on list body ✅
- PRO MOVE → RED FLAG — label logic fixed in Haiku prompt ✅
- Motion filename doubled — fixed (commit 56c02b7) ✅
- Job timeout — increased from 30 min → 90 min ✅
- cover_claim field — added to Brazil JSON schema (commit ac243eb) ✅
- Date injection — Haiku now gets today's date, won't invent 2025 (commit 9c8bb0f) ✅
- Series tag map — "dados-ou-agenda" → "Dados vs Opinião" fixed (commit 1ebe8bb) ✅
- Bio-initials fallback — renders when all image providers fail ✅
- Reviewer false positives — 3 false-positive checks fixed (commits b41a051, 00832a2, 03f3df0) ✅
- Template gallery — wired.html, index.html, selected.html all working ✅
- Cream variant contrast — FIX applied in opc_tip_base.css ✅

BROKEN / QUALITY ISSUES ❌
- Photo catalog 401 Unauthorized — photo_matcher.py uses wrong credential (service account instead of SHEETS_TOKEN OAuth) → real OPC project photos never load, DALL-E generates generic images instead
  File: scripts/content_creator/photo_matcher.py
- Per-slide image queries — Haiku emits one query for whole carousel, not per-slide → all slides get same generic image
  File: scripts/content_creator/carousel_builder.py → build_content_brief() Haiku prompt
- @HANDLE_PLACEHOLDER — visible in final PNGs when NanoBanana/Seedream fail; 2 retries added but base issue unresolved for live posts
  File: carousel_builder.py — cover slide HTML generation
- Cover hook weak — no number, no pain point (e.g. "What to look for before you sign anything" — nobody stops scrolling for this)
  File: carousel_builder.py — OPC Haiku prompt for cover hook

UNTESTED 🟡
- approval_handler.py — Gmail reply detection → Buffer scheduling
- Build history dedup — same topic can repeat across runs (no memory between runs)

---

## 4. ACTIVE TASK LIST

DONE ✅ (this session + prior fix sessions)
- Caption generation, stat overflow, SWIPE overlap, PRO MOVE label, date injection, series tag naming, reviewer false positives, motion filename, 90min timeout — all fixed
- wired.html wireImages() 5 bugs fixed (commit 630c485)
- selected.html created — OPC Tip of the Week locked as first chosen template (commit 977bc96)
- Full Circle Plan restructured to 11 sections, templates-first
- Tip of the Week = flat category, no sub-series needed
- news_brazil_standalone.html — NEW standalone editorial template (InBr-style, PT-BR, 6 slides, gold/green/red palette, bio-cards, quote marks) (commit 3b058a6, 2026-05-02)
- news_usa_standalone.html — NEW standalone editorial template (EN, signal red/deep navy, 6 slides, same structure as Brazil) (commit 3b058a6, 2026-05-02)
- selected.html — added News Brazil + News USA + News Standalone groups with palette swatches (commit 3b058a6, 2026-05-02)
- Template Registry doc updated (appended TEMPLATE 11 + 12 + palette swatch reference, no overwrite)

IN PROGRESS 🔄
- Template walkthrough: Tip ✅ → Standalone Brazil/USA ✅ → next: opc_progress, opc_illustrated, opc_cutout, then remaining News series templates
- Building selected.html — News Standalone added; OPC Progress/Illustrated/Cutout still pending

PENDING ⏳ (priority order — top = most impactful)
1. Fix photo_matcher.py 401 — swap service account for SHEETS_TOKEN OAuth so real OPC photos load
   File: scripts/content_creator/photo_matcher.py
2. Fix per-slide image queries — Haiku prompt in build_content_brief() must emit context_image_query per slide
   File: scripts/content_creator/carousel_builder.py
3. Fix @HANDLE_PLACEHOLDER — Dados ou Agenda: main.py build_html() call missing handle arg; content["source_handle"] is computed but never passed (silently ships @HANDLE_PLACEHOLDER on every Dados PNG)
   File: scripts/content_creator/main.py — build_html() call in process_one_topic() ~line 1002
   NOTE: OPC templates hardcode @oakparkconstruction in HTML directly, don't use param. Brazil/USA use the param. Investigate which series shows placeholder in prod PNGs first.
4. Fix cover hook — OPC Haiku prompt must require: number OR dollar figure OR named consequence in hook
   File: scripts/content_creator/carousel_builder.py — add rule to hook field same as subhead (lines ~345-355)
5. Add build history dedup — write one row to Build History tab (Ideas & Inbox) after each build, check last 30 days before picking topic
   File: scripts/content_creator/main.py
6. Complete template walkthrough (opc_progress → illustrated → cutout → News templates) → update selected.html
7. Fix opc_progress.html — add .before-slot/.after-slot split panel
8. Fix news_brazil_shared.html + news_usa_shared.html — add sticker slot HTML on cover slide
9. Queue hygiene — 946 blank rows in Inspiration Library, 11 foreign TotW misfires

BLOCKED 🚫
- Approval → Buffer flow — untested, needs approval_handler.py Gmail trigger confirmed working first

---

## 5. FIX & TEST PROTOCOL

1. Fix one thing only.
2. Test it — open in browser or run the script.
3. Confirm it works — describe what you saw.
4. Commit with a clear message.
5. Move to next item.

NEVER fix two things in one commit.
NEVER mark done without a test result.
NEVER skip the confirm step.

---

## 6. CURRENT FIX PHASE

PHASE 1 — TEMPLATE LOCKDOWN (in progress)
Walk through every template in wired.html. For each: pick it or skip it. If picked → add to selected.html. Decide main category name. Decide if sub-series needed (usually no — the template IS the identity).
OPC: Tip of the Week ✅ locked → Progress / Illustrated / Cutout PENDING
News: all PENDING

PHASE 2 — WIRE (next after Phase 1)
For each chosen template in selected.html, confirm the pipeline builder (carousel_builder.py) maps to it correctly. Fix series naming, add missing schema fields.

PHASE 3 — RUN
Trigger content_creator.yml manually. Observe output. Report what came out of each layer.

PHASE 4 — REACT
Did the template render correctly? → If no: fix template layer.
Did the images export? → If no: fix export_slides.js.
Did the text generate? → If no: fix carousel_builder.py prompt.
Fix the failing layer. Retest. Never fix the wrong layer.

---

## 7. SESSION EXIT CHECKLIST

At every exit, append a note below under "SESSION NOTES" with:
- Date
- What was fixed or decided (one line per item)
- Any new IDs discovered
- Status snapshot change (what flipped from PENDING to DONE, or WORKING to BROKEN)
- One next action for next session

Update sections 3 and 4 above to reflect current state.

---

## 8. AIOX AGENT REVIEW — 2026-05-01

### AIOX-ARCHITECT FINDINGS

SKILL: Keep as-is. Add section gate markers (what must be true before each section starts).

AGENT: Do NOT build a standalone pipeline-fixer agent. Reasons:
- Tasks are code changes feeding a production pipeline. Auto-commit + auto-trigger = compounding silent failures.
- pipeline already has silent-failure problem (CLAUDE.md rule 17). Autonomous code-fixer on top = dangerous.
- The 4AM agent already handles nightly monitoring. Wire photo_matcher 401s and @HANDLE_PLACEHOLDER appearances into 🚨 Pipeline Failures tab. The 4AM agent reads that tab and can flag "fix is still broken after N runs" without touching code.

ARCHITECTURE FLAGS — these will break Section 4 (run) if not addressed first:

FLAG 1 (CRITICAL): Face slot is a Section 3 gate, not a post-run fix.
news_brazil_shared.html and news_usa_shared.html are missing the sticker-slot HTML element.
carousel_builder.py._build_news_shared_template_html() already calls the builder.
If you run Section 4 before P1 (face slot) is fixed → named-person rule violated on EVERY News carousel built.
Fix: Add sticker-slot HTML to news templates BEFORE wiring them. This is the first thing to do.

FLAG 2 (VERIFY FIRST): photo_matcher.py may already use SHEETS_TOKEN correctly.
The PENDING fix says "wrong credential (service account)" but the code reads SHEETS_TOKEN at line 23.
Real issue is likely an expired token or stale GitHub secret, not wrong credential.
Before coding anything: run `~/bin/gh secret list --repo priihigashi/oak-park-ai-hub` and verify SHEETS_TOKEN exists and is current.

FLAG 3 (TIMING): Wire approval_handler.py → Buffer BEFORE starting daily 2:30 AM runs.
Currently untested. If daily runs start and approval is broken, output piles up in Drive with no path to posting.
Test the approval flow manually (send test email, reply APPROVE, confirm Buffer scheduling) before enabling automation.

### AIOX-DEV FINDINGS
[Completed 2026-05-01 — code-level review of top 5 pending fixes]

**FIX 1 — photo_matcher.py 401**
READY TO CODE: YES
Real root cause: `_get_token()` calls `json.loads(SHEETS_TOKEN)` then returns `data.get("access_token")` — returns empty string because SHEETS_TOKEN is the refresh credential JSON, not a live access token. It NEVER refreshes. Fix: replace `_get_token()` with the same refresh call pattern as `main.py::get_oauth_token()`. Do NOT change anything else.
Silent failure: returns empty string → prints "no SHEETS_TOKEN" → returns None → every run silently falls to DALL-E. No error thrown.

**FIX 2 — per-slide context_image_query**
READY TO CODE: YES
`context_image_query` already exists in Haiku schema (OPC prompt ~line 438, Brazil ~line 824). The OPC prompt shows static example queries — Haiku treats them as the answer template and returns the same query for all slides. Fix: at line 438 in the OPC prompt block, add explicit rule: "Each slide's context_image_query MUST be unique and describe only that slide's specific visual. Do NOT reuse queries across slides." Brazil prompts already enforce this. No schema change needed.
Silent failure: identical images on all slides, no error thrown.

**FIX 3 — @HANDLE_PLACEHOLDER (NEEDS INVESTIGATION FIRST)**
READY TO CODE: NO
OPC templates (carousel_builder.py lines ~2367, 2486, 2670, 2873) hardcode `@oakparkconstruction` directly in the HTML strings — they ignore the `handle` parameter entirely. Brazil/USA templates DO use the `handle` param. Before coding: identify which specific series is producing the visible `@HANDLE_PLACEHOLDER` in prod PNGs. The fix location depends on the answer: OPC = fix the hardcode, Brazil/USA = fix the `build_html()` call in main.py to pass `content.get("source_handle")`.

**FIX 4 — OPC cover hook number/dollar requirement**
READY TO CODE: YES
The number constraint already exists on the `subhead` field (line ~351): "MUST contain at least one of: a specific number, a dollar amount, or a named consequence." Add the identical rule to the `hook` field in the same OPC prompt block (search for `"hook"` around lines 345-355). One-line addition. No schema change.
Silent failure: vague hook ships, nobody stops scrolling.

**FIX 5 — Build History dedup**
READY TO CODE: NO
Three things missing before this can be coded: (a) confirm "Build History" tab actually exists in Ideas & Inbox spreadsheet — no script currently references it, no HISTORY_TAB variable; (b) define match field: exact topic string match? slug? both?; (c) confirm WHERE in main.py the check runs — before `pick_topics()` (preferred) vs before `process_one_topic()`. If after pick, a failed build blocks that topic for 30 days.

**NEW CRITICAL FINDING — Dados ou Agenda handle silently dropped (not in task list)**
`main.py` line ~1002: `build_html(content, niche, slug, str(work), media_paths=media_paths)` — NO `handle` argument passed.
For Dados ou Agenda series: `generate_carousel_content()` works hard to produce `content["source_handle"]` (with a retry loop). That value is extracted into the content dict but NEVER forwarded to `build_html()`. The handle the prompt generates is silently discarded. Result: `@HANDLE_PLACEHOLDER` renders on every Dados ou Agenda PNG and ships. No error. Add to PENDING task list — add as item 3a (higher priority than Fix 3 generic).

---

## 9. SECTION GATES (what must be true before each section starts)

SECTION 1 gate: Gallery (wired.html) loads all templates correctly ✅ DONE
SECTION 2 gate: Template is locked in selected.html for that series
SECTION 3 gate: Section 2 design gap for that template is fixed
SECTION 4 gate: At least one template is wired in Section 3 + approval_handler.py tested
SECTION 5 gate: Section 4 produced output with bad text (don't run preemptively)
SECTION 6 gate: Section 4 produced output with bad visuals (don't run preemptively)
SECTION 7 gate: Run anytime, doesn't block other sections

---

## 11. RULES FOR THIS SKILL

- APPEND ONLY — never delete prior session notes
- Every link/ID must be verified before adding — mark unknowns [NEEDS LINK]
- Keep language short and scannable — this is read on mobile
- No preamble at session start — just report status and next action

---

## SESSION NOTES

### 2026-05-01
- wired.html wireImages() 5 bugs fixed (commit 630c485)
- selected.html created, OPC Tip of the Week locked as first chosen template (commit 977bc96)
- Full Circle Plan restructured to 11 sections, templates-first (doc 17B8wc4wWmcBapl_4gduHyMzjYl4R4y_RYuvZae5xRPU)
- Decided: Tip of the Week = flat category, no sub-series needed
- AIOX-architect review complete: no standalone agent, 3 arch flags (face slot is Section 3 gate, verify photo_matcher secret, test approval handler before automation)
- AIOX-dev review complete: Fixes 1+2+4 ready to code. Fix 3 needs investigation first. Fix 5 needs Build History tab confirmed. New critical: Dados ou Agenda source_handle silently dropped at main.py build_html() call — added as item 3 in PENDING.
- Next: walk through opc_progress.html — does it need sub-series? Does before/after split panel need fixing before choosing it? (Section 2 design gap P4)
