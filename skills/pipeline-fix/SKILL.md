# SKILL: pipeline-fix
# Reads at session start. Report status immediately. No re-explaining needed.

---

## SESSION START — DO THIS FIRST
Read sections 3 and 4 below. Then say exactly:
"Pipeline-fix loaded. Status: [WORKING/BROKEN/UNTESTED]. Pending: [top 1–2 items]. Next action: [one thing]."
Do not ask what to do. Execute the next pending action.

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

## 2. PIPELINE ARCHITECTURE (end-to-end)

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
[Updated: 2026-05-01]

WORKING ✅
- Template gallery (index.html, wired.html) — page navigation functional
- wired.html wireImages() — 5 bugs fixed (commit 630c485), all 10 templates show with dummy images
- selected.html (Page 3 — Chosen) — created (commit 977bc96), OPC Tip of the Week locked in
- Full Circle Plan doc — saved to Drive (ID: 17B8wc4wWmcBapl_4gduHyMzjYl4R4y_RYuvZae5xRPU)
- email_preview.py — sends per-carousel emails with slide stack, clip failure warnings, caption block, APPROVE/REJECT commands

BROKEN / INCOMPLETE ❌
- opc_progress.html — missing .before-slot/.after-slot split panel (before-after layout is wrong)
- news_brazil_shared.html — CSS has .bio-card classes but no HTML elements for sticker/face slot on cover slide
- news_usa_shared.html — same gap as above
- scraper.py — uses `series_override = "Fact-Checked"` (mixed case) but main.py maps "FACT-CHECKED" (uppercase) → silent routing mismatch
- carousel_builder.py — generate_brazil_content() missing: cover_claim field in JSON schema, date injection, data_confidence flag
- email_preview.py — APPROVE/NOT GOOD/REJECT reply commands are buried below all slide images (should be at top)

UNTESTED 🟡
- Full pipeline end-to-end (template → export → upload → email → approve → Buffer)
- motion_sources.py 8-tier clip cascade
- approval_handler.py Gmail reply detection

---

## 4. ACTIVE TASK LIST

DONE ✅
- Fix wireImages() 5 bugs in wired.html (commit 630c485)
- Create selected.html with OPC Tip of the Week as first chosen template (commit 977bc96)
- Update Full Circle Plan doc with new 11-section structure (templates-first)
- Decide OPC Tip of the Week = main category, no sub-series needed

IN PROGRESS 🔄
- Template walkthrough: OPC done → next is OPC Progress (before/after) + OPC Illustrated + OPC Cutout
- Building selected.html — adding approved templates one by one as walkthrough proceeds

PENDING ⏳
- Walk through OPC templates: opc_progress.html, opc_illustrated.html, opc_cutout.html — decide main category names, add chosen ones to selected.html
- Walk through News templates: news_brazil.html, brazil_motion.html, news_brazil_shared.html, news_usa_shared.html, who_is.html, the_case.html
- Fix opc_progress.html — add .before-slot/.after-slot split panel (Priority 4 from design audit)
- Fix news_brazil_shared.html + news_usa_shared.html — add sticker slot HTML elements (Priority 1 — unblocks 5 series)
- Fix scraper.py series_override case mismatch ("Fact-Checked" → "FACT-CHECKED")
- Fix carousel_builder.py — add cover_claim, date injection, data_confidence to schema
- Move APPROVE/REJECT reply commands to top of email_preview.py email body
- Fix series naming everywhere: "Dados vs Opinião" (was "Dados ou Agenda?")
- Wire chosen templates to pipeline (Section 3 of Full Circle Plan)
- Run pipeline end-to-end and observe output
- Queue hygiene: 946 blank rows in Inspiration Library, 11 foreign TotW misfires

BLOCKED 🚫
- Can't run pipeline until templates are wired (selected.html walkthrough must finish first)
- Can't fix news_brazil_shared / news_usa_shared until those templates are chosen in walkthrough

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

## 8. RULES FOR THIS SKILL

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
- Next: walk through opc_progress.html — does it need a sub-series? Does the before/after split panel need to be fixed before we can choose it?
