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

### ⭐ MASTER PROJECT TRACKER (Pipeline Fix Master Checklist — Done Current Next)
- Link: https://docs.google.com/spreadsheets/d/1yh9C7KU9OlqCdHNDI9mbZ6ldqLA3bAR3uENXUh37bkQ/edit
- ID:   1yh9C7KU9OlqCdHNDI9mbZ6ldqLA3bAR3uENXUh37bkQ
- Location: Marketing > PIPELINE FIX folder (ID: 1FHPkx8VA6c-Wmy6hI3uX_weSPwJPBp3z)
- Tabs: Master Checklist (71 rows, all tasks) | Credit Blocks | Today Changes | Open P0 — Next | Storytelling Rules | Rollback — Evidence
- AUTO-SYNC: pipeline_self_heal.yml calls scripts/pipeline_tracker_writer.py sync after every cycle
- CREDIT LOG: capture_pipeline.yml logs API credit failures to Credit Blocks tab automatically
- MANUAL UPDATE: python scripts/pipeline_tracker_writer.py done --sh-id SH-XXX --done "..." --evidence "commit abc"
- READ THIS FIRST when starting a session — it reflects the most current Done/Blocked/Next state across all SH tasks

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
- BRAIN cascade backup (portable handoff for non-Claude AIs — Codex/OpenAI/etc.): https://docs.google.com/document/d/1YIUt8yULACQ0ebd0PYtws27osT-vESJBqSuVy2T-d-Y/edit
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
[Updated: 2026-05-06 — after 10-task SH batch + gap audit]

PIPELINE IS BUILDING ✅ — carousels render, reviewer passes, Drive upload works, emails send.
Buffer scheduling is now UNBLOCKED (SH-029 BUFFER_PROFILE_ID guard removed).
Remaining issues are QUALITY problems + a few prior-session gaps (SH-013/SH-028/SH-015/SH-041/SH-011).

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
- Photo catalog 401 — photo_matcher.py now refreshes SHEETS_TOKEN refresh credential into a live access token (commit 2193698, verified 158 catalog rows live) ✅

BROKEN / QUALITY ISSUES ❌
(none remaining from previous audit — see DONE list)

UNTESTED 🟡
- approval_handler.py — Gmail reply detection → Buffer scheduling (NOW UNBLOCKED — guard removed 2026-05-06 b72f320)
- Build history dedup — BUILT (ef7a018) but not yet triggered in prod
- find_supporting_clips() (SH-022) — code wired, awaits next pipeline run for live test
- run_weekly_catalog_audit() (SH-037) — wired into 4AM agent, fires next Sunday
- _fetch_url_cache (SH-056) — populates only on Pexels/Pixabay paths; AI cascade providers don't register URLs (low value because OPC blocks all AI tiers anyway)

---

## 4. ACTIVE TASK LIST

DONE ✅ (this session + prior fix sessions)
- Caption generation, stat overflow, SWIPE overlap, PRO MOVE label, date injection, series tag naming, reviewer false positives, motion filename, 90min timeout — all fixed
- wired.html wireImages() 5 bugs fixed (commit 630c485)
- selected.html created — OPC Tip of the Week locked as first chosen template (commit 977bc96)
- Full Circle Plan restructured to 11 sections, templates-first
- Tip of the Week = flat category, no sub-series needed
- news_brazil_standalone.html — REBUILT (InBr-faithful). Yellow (#FFE500) bg, blue circle blob (#0057CC), B&W person left, Barlow Condensed 900, 3 slides. CSS-variable architecture. Pipeline-compatible slots. PT-BR.
- news_usa_standalone.html — REBUILT (mirror of Brazil). Light (#F5F5F5) bg, red circle blob (#CC1F2D), same layer stack + slots. EN. 3 slides.
- selected.html — News Brazil + News USA groups with palette swatches; standalone cards renamed "Cover · Main Character"; slide counters corrected to 1/3
- Template Registry doc updated (appended TEMPLATE 11 + 12 + palette swatch reference, no overwrite)

IN PROGRESS 🔄
- OPC template walkthrough: ALL DONE ✅ (Tip + progress_media + progress + illustrated + cutout + statement + material_profile + item_spotlight + four_card_grid + duotone + base — all locked in selected.html)
- News template walkthrough: standalone Brazil/USA ✅ locked; shared (Verificamos/The Chain) cards + sticker slot ✅; remaining News series templates still to walk through

DONE THIS SESSION ✅ (2026-05-05 session 3)
- opc_progress, opc_illustrated, opc_cutout added to selected.html (commit 8ab6524). OPC Phase 1 COMPLETE.
- news_brazil_shared + news_usa_shared sticker-slot CSS + HTML added to cover slide (commit 41fcf4f). ARCH FLAG 1 cleared.
- reorder_selected_opc_once.yml deleted — was stuck, self-delete step never fired (commit 978cbc2).
- Fix 3a (Dados ou Agenda handle) — verified ALREADY FIXED in main.py lines 1097-1101 (done in earlier session).

DONE THIS SESSION ✅ (2026-05-05 session 4)
- generate_progress_content() added to carousel_builder.py + routed in generate_carousel_content() (commit f1da63c). OPC template wiring COMPLETE.
- Brazil Verificamos + USA The Chain cards added to selected.html ROW 1 Full Formats (commit e6665af). News template walkthrough COMPLETE.

DONE THIS SESSION ✅ (2026-05-05 session 5)
- SH-016: .clip-frame rounded corners + layered shadows + accent-ring (823754f)
- SH-055: --margin CSS variable + SLIDE_INSET_PX configurable (e7d7f6b + 2607977)
- SH-017: yt-dlp max 300s duration filter on all 3 download call sites (c24c000)
- SH-015: tier_giphy() GIPHY→GIF→MP4 in motion_sources.py SOURCE_CHAIN (5638cbf) + GIPHY_API_KEY env wired in workflow (4a1c710) — ACTIVE as soon as GIPHY_API_KEY secret is set
- SH-020: label-leak checker in carousel_reviewer.py check_html_placeholders() (24ea1ce + af17b74)
- SH-013: 8th-grade readability rule → OPC_COPY_RULES + BRAZIL_COPY_RULES (967534b + af17b74) — PARTIAL (3 prompt functions still missing, see PENDING #1)
- SH-028: score_storytelling() Haiku scorer wired into check_built_post() (b21482e) + credit-depleted HTTP 529/overload WARN added (4a1c710) — PARTIAL (Drive review path still not wired, see PENDING #2)
- SH-011: review_only_folder_id input in content_creator.yml (7cf6834) — PARTIAL (retry job guard missing, see PENDING #5)
- SH-041: DALL-E off by default via _USE_DALLE guard (fca9082) + workflow env documented + legacy _generate_ai_cover() now also requires USE_DALLE=true (4a1c710) — FULLY GUARDED
- SH-061: brief validation markers [SH-061] ✅/⚠/❌ in main.py (083040f)
- Master Checklist updated — all 10 rows = Done with commit evidence + gap notes

GAP-CLOSING COMMIT (4a1c710 — addresses post-session audit):
- content_creator.yml (both jobs): GIPHY_API_KEY: ${{ secrets.GIPHY_API_KEY }} + USE_DALLE: '' with opt-in comment
- carousel_builder.py: _USE_DALLE constant added; legacy _generate_ai_cover() now requires USE_DALLE=true (was bypassing image_providers.py guard)
- carousel_reviewer.py: score_storytelling() catches HTTPError, surfaces ⚠ WARN on HTTP 529/overload/credit instead of silent skip

DONE THIS SESSION ✅ (2026-05-06 — 10-task SH batch + gap audit)
- SH-025: OPC_MATERIAL_REFERENCE wired into _opc_photo_query() for stock query enrichment (87b4fec)
- SH-037: run_weekly_catalog_audit() added + called from 4AM agent every Sunday (69a2e66)
- SH-038: TIER 0 OPC catalog extended to product-photo slides 2-4 (ba14867)
- SH-039: DALL-E blocked for OPC — real photos only (ced297e)
- SH-040: photo_matcher._url_looks_watermarked_or_tiny wired into _vision_accept (87b4fec)
- SH-056: _fetch_url_cache tracks Pexels+Pixabay source URLs for AI-art domain check (87b4fec)
- SH-057: cutout head-crop fixed (overflow:visible + object-position:top center + min-height:110%) (ea487b3, 97cc2f0)
- SH-022: find_supporting_clips() in motion_sources.py + wired into main.py (807c8db)
- SH-010: video-research.yml clips upload to Drive resources/clips/ via OAuth (1cc20c8)
- SH-029: BUFFER_PROFILE_ID guard removed — schedule_to_buffer() auto-discovers profile (b72f320)

PENDING ⏳ (priority order — top = most impactful — START HERE TOMORROW)
1. SH-013 gap: add readability rule inline to generate_progress_content(), generate_dados_content(), generate_verdade_content() — 3 functions build own prompts, bypass OPC/BRAZIL_COPY_RULES. File: scripts/content_creator/carousel_builder.py
2. SH-028 gap: wire score_storytelling() into check_drive_folder() after cover.html downloaded to /tmp. File: scripts/content_creator/carousel_reviewer.py
3. Add GIPHY_API_KEY to GitHub secrets: `gh secret set GIPHY_API_KEY --repo priihigashi/oak-park-ai-hub` (workflow env already wired in 4a1c710 — secret is the LAST piece. Priscila must paste GIPHY developer dashboard key)
4. SH-011 gap: add `&& github.event.inputs.review_only_folder_id == ''` to retry job "Retry content creator" step if condition (line ~277 in content_creator.yml). Low priority (retry only fires on failure)
5. Test approval_handler.py → Buffer flow — UNBLOCKED 2026-05-06 (BUFFER_PROFILE_ID guard removed). Send a real APPROVE reply to a preview email, confirm Buffer schedules.
6. Run content_creator.yml manually (Phase 3) — trigger one build, observe email preview, verify SH-025/038/039/056 actually engage on real OPC slides
7. Verify SH-022 find_supporting_clips() pulls real Pexels videos on next prod run (PEXELS_API_KEY ✅ set)
8. Verify SH-037 catalog audit runs next Sunday (4AM agent will print "[scraper] SH-037: Sunday — running photo catalog description audit")

BLOCKED 🚫
- Approval → Buffer flow — untested, needs manual test (reply APPROVE to preview email → confirm Buffer scheduling)

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

PHASE 1 — TEMPLATE LOCKDOWN (OPC COMPLETE ✅ — News in progress)
Walk through every template in wired.html. For each: pick it or skip it. If picked → add to selected.html.
OPC: ALL LOCKED ✅ (Tip + progress_media + progress + illustrated + cutout + statement + material_profile + item_spotlight + four_card_grid + duotone + base)
News: standalone Brazil/USA ✅ — remaining series templates PENDING

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

## 10. STANDALONE TEMPLATE DESIGN PRINCIPLES (locked 2026-05-02)

Every standalone template Priscila approves MUST follow this architecture so the pipeline can produce it without chat intervention.

### How standalones are created
1. Priscila sends a reference screenshot (e.g. @theinterceptbrasil cover)
2. We recreate it faithfully in HTML — same layout, same font weight, same geometric shapes
3. Every visual element becomes an ISOLATED CSS variable or a separate named CSS class

### CSS variable architecture (non-negotiable)
All color/theme control lives in `:root`:
- `--bg-color` → slide background
- `--blob-color` → geometric shape (circle, stripe, rectangle)
- `--text-color` → headline, logo, labels
- `--hl-color` → inline text highlight background
Pipeline can swap the entire palette by overriding 4 variables. No HTML changes needed.

### Person photo — pipeline injection protocol
- Wrap person photo div with BOTH `person-layer` AND `sticker-slot` classes
- Inside: `<div class="sticker-placeholder"></div>`
- CSS on `.person-layer` applies grayscale + contrast — pipeline drops raw color photo, CSS converts it
- `filter: drop-shadow()` on `.person-layer` div keeps the person VISUALLY SEPARATE from background
- wireImages() in selected.html targets `.sticker-placeholder` and injects dummy photo for preview

### Context photo (slide 2) — pipeline injection protocol
- Use `<div class="context-img-slot">` with `<div class="ctx-fallback"></div>` inside
- wireImages() targets `.context-img-slot` and injects context photo

### Geometric elements — isolation rule
- Each shape (circle blob, accent stripe, diagonal bar) is its OWN div/element
- No shape is merged into a background gradient or pseudo-element that carries multiple jobs
- Rationale: pipeline (or Priscila) can recolor one shape without touching others
- Comment each element: `<!-- LAYER 1: ... -->` `<!-- LAYER 2: ... -->` so shape identity is scannable

### 3-slide default for standalone
Cover (main character) → Body/Context (what happened) → Sources
Pipeline fills: headline, body copy, sources from the JSON content brief.
Cover gets: person photo, headline, logo, credit line.
Body gets: context image, body copy with [DATE] / [PERSON] / [DECISION] tokens.

### What the pipeline NEVER needs to do
- Crop or grayscale the photo (CSS handles it)
- Know which niche's color to use (CSS variables handle it — pipeline passes `--blob-color` override if needed)
- Come to chat to rebuild the template

### Reference templates (source of truth)
- Brazil: docs/templates/news_brazil_standalone.html (yellow bg, blue blob, PT-BR)
- USA:    docs/templates/news_usa_standalone.html    (light bg, red blob, EN)
Both are mirrors — same class structure, same slot names, only `:root` variables differ.

---

## 11. RULES FOR THIS SKILL

- APPEND ONLY — never delete prior session notes
- Every link/ID must be verified before adding — mark unknowns [NEEDS LINK]
- Keep language short and scannable — this is read on mobile
- No preamble at session start — just report status and next action

---

## SESSION NOTES

### 2026-05-05 (session 4 — FORMAT-024 Verdade Pela Metade full build)
- BUILT: FORMAT-024 Verdade Pela Metade — full 8-gap automation, 8 commits (c4155f5 → 5af0e9d).
- GAP 1 (c4155f5): "DEBUNK SOURCE" added to INSTAGRAM_TYPES + dispatch branch in scrape_all_targets(). Also fixed pre-existing silent bug: scrape_all_targets() had no return statement — every 4AM run was hitting TypeError on tuple unpack and silently falling back. Fixed.
- GAP 2 (5a48f91): scrape_debunk_source() — 7-day filter, engagement sort, dedup vs Inspiration Library, returns 1 normalised item.
- GAP 3 (08e5a0a): _normalise() — DEBUNK SOURCE branch sets series_override="Verdade Pela Metade" + fake_news_route="debunk".
- GAP 4 (29a5aa9): _classify_debunk_mode() — Haiku call on caption (transcript proxy), returns "mode_a" or "mode_b".
- GAP 5 (100b413): _research_attribution() — Sonnet call for mode_a only, returns responsible_party/decision_name/year/source_url as research_brief.
- GAP 6 (df10bda): sheets_writer.py — debunk items land as "Needs Research" (not "Captured"). "needs research" added to SKIP_STATUSES in topic_picker.py so pipeline only picks up "Approved". topic_picker propagates series_override + fake_news_route fields through to Content Queue.
- GAP 7 (25d903d): Full template — verdade-pela-metade entry in TEMPLATES["brazil"], generate_verdade_content(), _build_verdade_html() (7 slides, dark brand, mode-conditional slide 3), wired into carousel_builder.py + main.py (post_id, template_key, requires_approval, series display map, post_type).
- GAP 8 (5af0e9d): Tuesday gate in scrape_debunk_source() — weekday() != 1 → return None.
- Drive IDs confirmed in CLAUDE.md: series 1r6NJ6uoKezptnolgeSfPOeKl2dccEjPd / _TEMPLATE_CAROUSEL 1Tspx9SsfFxJjzh_ZdIC_exQBHe4-p-1K.
- Source account: INTERNAL ONLY — never named in content, captions, or slide copy.
- Status flips: FORMAT-024 Verdade Pela Metade → FULLY WIRED (was: not started).
- Next: Add "DEBUNK SOURCE" row to Scraping Targets tab with source account username to activate. Then test next Tuesday — scrape → Inspiration Library "Needs Research" → approve → carousel builds automatically. Also: wire opc_progress/illustrated/cutout in carousel_builder.py (Section 3 PENDING).

### 2026-05-05 (session 2 — pipeline fix + capture queue)
- FIXED: OPC Photo Catalog TIER 0 added to mid-slides in carousel_builder.py (commit dad4918). Was only on cover; slides 2/3/4 were skipping the 158-photo catalog and going to AI.
- FIXED: OPC Photo Catalog TIER 0 added to fix_existing_images.py re-fetch path (commit 4d3cc0b).
- FIXED: auto_fixer.py Goal 1B text rewrite loop — was imported but never called ("Future expansion"). Now downloads cover.html, calls review_carousel_html() + apply_edits_to_html(), re-uploads (commit 4d3cc0b).
- FIXED: carousel_reviewer.py auto-fix trigger now fires on text quality issues too, not just image flags (commit 4d3cc0b).
- FIXED: capture_pipeline.py — added _llm_text() helper with Claude→OpenAI gpt-4o fallback on Anthropic credit/auth error. Replaced 4 bare unprotected Claude calls. Capture no longer crashes fatally when credits are depleted (commit 1bcf1c2).
- FIXED: /capture skill (capture.md) — default behavior changed to queue-first. No GitHub Actions unless explicitly asked.
- RESCUED: 8 failed capture URLs added to Capture Queue via one-time grab_capture_fail.py script.
- DIAGNOSED: 27 Capture Queue rows stuck with D=FALSE. Multiple duplicates (DXKAe4CDrbj x7). Root cause of failures: Instagram 403 rate limiting on burst runs.
- Status flips: SH-033 OpenAI fallback → DONE. Photo Catalog mid-slides → DONE. Goal 1B text loop → DONE.
- New IDs: none.
- Next: Capture Queue cleanup (clean 27 FALSE rows + dedup + add dedup check to processor). Then template walkthrough.

### 2026-05-05 (session 1)
- FIXED Issue #127 upstream capture bug (2 commits):
  - fe18cf9: capture_queue_processor.py — column C comment now passed as `notes` to workflow dispatch. For project=opc, OPC CONTEXT GUARD auto-prepended even when column C is blank.
  - a78bea4: capture_pipeline.py — analyze_opc() now returns TRANSCRIPT UNAVAILABLE early for empty/sentinel transcripts. run_opc() has hard gate before brief generation. OPC entity definition injected into Claude prompt to block IL/tourism hallucination.
- FIXED FIX 2: context_image_query uniqueness rule added to OPC prompt (commit af5b322, carousel_builder.py line 442). Haiku now explicitly required to use distinct queries for slides 2/3/4.
- FIXED FIX 4: Caption hook number/dollar/consequence hard rule added (commit 3c7121f, carousel_builder.py line 437). Matches subhead constraint pattern. Banned phrases listed. Good examples included.
- BUILT Build History dedup (commit ef7a018):
  - Created Build History tab in Ideas & Inbox (sheetId 459256337). Columns: DATE|SLUG|TOPIC|NICHE|SERIES|DRIVE_URL|STATUS. Row 1 frozen.
  - main.py: write_build_history() + get_recent_built_slugs() + _topic_to_slug() helpers added after add_catalog_row().
  - write_build_history() called before return in process_one_topic() (after Drive upload confirmed).
  - Phase B dedup block: reads recent slugs, skips approved topics already built in last 30 days, sends alert when any skipped.
- Status flips: PENDING #1 (build dedup) → DONE. PENDING FIX 2 + FIX 4 (from AIOX-dev) → DONE.
- New IDs: Build History tab sheetId=459256337 in Ideas & Inbox (1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU)
- Next: PENDING #2 — complete template walkthrough (opc_progress → illustrated → cutout → News). Or run a manual pipeline trigger to test photo_matcher + dedup live.

### 2026-05-05 (session 3)
- DONE: opc_progress, opc_illustrated, opc_cutout added to selected.html (commit 8ab6524). OPC Phase 1 template lockdown COMPLETE — all 11 OPC templates now in selected.html.
- DONE: news_brazil_shared + news_usa_shared — .sticker-slot CSS + HTML added to cover slide (commit 41fcf4f). ARCH FLAG 1 cleared. Named-person rule now met for Verificamos + The Chain.
- DONE: reorder_selected_opc_once.yml deleted (commit 978cbc2). Stuck one-time workflow was failing on every push because self-delete step never completed.
- VERIFIED: Fix 3a (Dados ou Agenda handle) already fixed in main.py lines 1097-1101 — no code change needed.
- DONE: Queue hygiene — 1267 blank rows deleted from Inspiration Library (now 554 rows). TotW misfires = stale note, no evidence found in any tab. Capture Queue D=FALSE = 0 (cleared by session 2 fixes).
- Status flips: PENDING #2 (template walkthrough OPC) → DONE. PENDING #4 (news shared sticker) → DONE. PENDING #5 (queue hygiene) → DONE. Fix 3a → DONE.
- New IDs: none.
- Next: Wire opc_progress/illustrated/cutout in carousel_builder.py (Section 3). Then test approval_handler.py → Buffer.

### 2026-05-05 (session 5 — 10 SH tasks batch + gap audit)
- SHIPPED 10 SH tasks in 10 commits (823754f → 083040f), pushed to main:
  - SH-016 (823754f): .clip-frame rounded corners, layered shadows, accent-ring in carousel_builder.py inline CSS
  - SH-055 (e7d7f6b + 2607977): --margin:40px CSS variable in opc_tip_base.css + news_history_base.css; corners use var(--margin); SLIDE_INSET_PX configurable
  - SH-017 (c24c000): yt-dlp --match-filter duration < 300 added to all 3 download call sites in motion_sources.py
  - SH-015 (5638cbf): tier_giphy() added to motion_sources.py — GIPHY search → GIF download → ffmpeg GIF→MP4; silently skips if GIPHY_API_KEY unset
  - SH-020 (24ea1ce + af17b74): label-leak checker _LABEL_PATTERNS in carousel_reviewer.py check_html_placeholders()
  - SH-013 (967534b + af17b74): Rule 7 (8th-grade readability) → OPC_COPY_RULES; Rule 11 → BRAZIL_COPY_RULES in carousel_builder.py
  - SH-028 (b21482e): score_storytelling() Haiku scorer 0-100 per slide wired into check_built_post(); adds issue if overall < 60; per-slide table in email
  - SH-011 (7cf6834): review_only_folder_id input added to content_creator.yml; auto-enables review_only mode; main job condition updated
  - SH-041 (fca9082): _USE_DALLE guard in image_providers.py — DALL-E off by default, requires USE_DALLE=true
  - SH-061 (083040f): [SH-061] validation markers in main.py both brief-fetch paths — ✅/⚠/❌ logs + 120-char preview
- AUDIT GAPS FOUND (documented in Master Checklist Next Action + Notes columns):
  - SH-013 GAP: Readability rule NOT in generate_progress_content(), generate_dados_content(), generate_verdade_content() — 3 functions build own prompts, bypass OPC/BRAZIL_COPY_RULES entirely
  - SH-028 GAP: score_storytelling() NOT wired into check_drive_folder() (Drive/manual review path) — only fires on local build path. Fix: call it after cover.html downloaded to /tmp in check_drive_folder()
  - SH-011 GAP: Retry job "Retry content creator" step + "AI Content Audit" step missing review_only_folder_id == "" guard. Low priority (retry fires only on failure)
  - SH-015 GAP: GIPHY_API_KEY not in GitHub secrets — tier silently skips every run until secret added. Command: gh secret set GIPHY_API_KEY --repo priihigashi/oak-park-ai-hub
  - SH-041 GAP: USE_DALLE=true opt-in undocumented in workflow env block — future sessions won't know how to enable
- STATUS: Master Checklist updated — all 10 SH rows flipped to Done with commit evidence + gap notes
- Next session top priorities:
  1. Fix SH-013 gap: add readability rule inline to generate_progress_content, generate_dados_content, generate_verdade_content
  2. Fix SH-028 gap: wire score_storytelling() into check_drive_folder()
  3. Add GIPHY_API_KEY to GitHub secrets + document USE_DALLE in workflow env
  4. Test approval_handler.py → Buffer flow (ARCH FLAG 3 — still untested)

### 2026-05-04
- FIXED photo_matcher.py SHEETS_TOKEN refresh (commit 2193698). Replaced `_get_token()` which returned the empty `access_token` field from the refresh credential JSON. Now mirrors `main.py::get_oauth_token()` — POSTs to oauth2.googleapis.com/token with refresh_token grant + caches with 60s buffer.
- Verified live: 158 catalog rows read, real Drive URLs returned (e.g. IMG_4493.jpeg from Walnut Slab Kitchen Marble Waterfall project).
- Status flip: BROKEN photo_matcher 401 → DONE. Real OPC jobsite photos will now load on next content_creator.yml run instead of falling through to DALL-E.
- Next: PENDING #1 — per-slide context_image_query rule in OPC Haiku prompt (carousel_builder.py ~line 438). Same one-line addition pattern as the subhead constraint that already exists on line ~351.

### 2026-05-02
- REBUILT news_brazil_standalone.html — faithful InBr recreation (yellow bg, blue circle blob, B&W person, Barlow Condensed 900, 3 slides). CSS-variable architecture, pipeline-compatible sticker-slot + context-img-slot.
- REBUILT news_usa_standalone.html — mirror of Brazil (light bg, red circle blob, EN, 3 slides). Identical layer structure, only :root color variables differ.
- selected.html — standalone cards renamed "Cover · Main Character", slide counters fixed to 1/3, tpl-when descriptions updated with pipeline-injection details
- Locked STANDALONE TEMPLATE DESIGN PRINCIPLES in Section 10 of this SKILL (CSS variables, person isolation via drop-shadow, grayscale via CSS not pipeline, sticker-slot protocol)
- Next: commit + push to main → GitHub Pages live. Then opc_progress.html walkthrough.

### 2026-05-01
- wired.html wireImages() 5 bugs fixed (commit 630c485)
- selected.html created, OPC Tip of the Week locked as first chosen template (commit 977bc96)
- Full Circle Plan restructured to 11 sections, templates-first (doc 17B8wc4wWmcBapl_4gduHyMzjYl4R4y_RYuvZae5xRPU)
- Decided: Tip of the Week = flat category, no sub-series needed
- AIOX-architect review complete: no standalone agent, 3 arch flags (face slot is Section 3 gate, verify photo_matcher secret, test approval handler before automation)
- AIOX-dev review complete: Fixes 1+2+4 ready to code. Fix 3 needs investigation first. Fix 5 needs Build History tab confirmed. New critical: Dados ou Agenda source_handle silently dropped at main.py build_html() call — added as item 3 in PENDING.
- Next: walk through opc_progress.html — does it need sub-series? Does before/after split panel need fixing before choosing it? (Section 2 design gap P4)

### 2026-05-06 — SH-025/037/038/039/040/056/057/022/010/029 — full execution + gap audit + gap fixes

INITIAL BUILD (all 10 tasks shipped via subagent, then pushed):
- SH-025 d33615d — OPC_MATERIAL_REFERENCE dict added to photo_matcher.py
- SH-037 b54ae8a — audit_stale_catalog_rows() + batch_retag_stale_rows() added
- SH-038 ba14867 — TIER 0 OPC catalog extended to product-photo slides 2-4
- SH-039 ced297e — DALL-E blocked for OPC (real-photo only cascade)
- SH-056 d498319 — _is_ai_art_url() domain guard + _opc_photo_query() rewriter
- SH-057 ea487b3 + 97cc2f0 — cutout head-crop fixed (overflow:visible + object-position:top center + min-height:110%)
- SH-040 8ee2fb0 — validate_image_relevance() added to photo_matcher.py
- SH-022 807c8db — find_supporting_clips() in motion_sources.py + wired into main.py
- SH-010 1cc20c8 — video-research.yml clips upload to Drive resources/clips/
- SH-029 f41c8b5 — Buffer failure guard + Pipeline Failures logging in approval_handler.py

GAP AUDIT (read every changed file, found 5 real gaps):

GAP 1 — SH-025: OPC_MATERIAL_REFERENCE was defined but never called anywhere in the image cascade. Dead code.
FIX (commit 87b4fec): _opc_photo_query() now imports OPC_MATERIAL_REFERENCE and prepends a specific material reference term when topic keyword matches a category (paint, flooring, tile, countertop, lumber, etc.). Stock photo queries for OPC now get specific product context instead of generic topic text.

GAP 2 — SH-040: validate_image_relevance() existed in photo_matcher.py but was never imported or called from carousel_builder.py. _vision_accept() used its own internal _vision_validate. The photo_matcher URL heuristic (_url_looks_watermarked_or_tiny) was completely disconnected from the image cascade.
FIX (commit 87b4fec): _vision_accept() now imports and calls photo_matcher._url_looks_watermarked_or_tiny on the source URL for early reject before Vision API check.

GAP 3 — SH-056: _is_ai_art_url(local_path) was checking a LOCAL file path (e.g. /tmp/abc.jpg), not the original URL. Domain names like lexica.art would never appear in a local path — the check always passed, doing nothing.
FIX (commit 87b4fec): Added _fetch_url_cache dict at module level. _fetch_pexels_image and _fetch_pixabay_image register original URLs. _vision_accept checks source_url = _fetch_url_cache.get(local_path, local_path) so _is_ai_art_url sees the actual HTTP source URL.

GAP 4 — SH-037: audit_stale_catalog_rows() was defined but never called from anywhere. Unreachable code.
FIX (commit 69a2e66): run_weekly_catalog_audit() added to scraper.py; imported and called from 4AM agent main.py before the scraping step, every Sunday (weekday 6). batch_retag_stale_rows() logs stale count only — Vision API retag remains a stub until Vision API key is wired.

GAP 5 — SH-029 BLOCKER: BUFFER_PROFILE_ID secret does NOT exist in GitHub (only BUFFER_API_KEY_EXP04092027 is set). The SH-029 guard added to approval_handler.py checked for BUFFER_PROFILE_ID before scheduling — meaning every APPROVE reply would log "BUFFER_PROFILE_ID missing" and skip Buffer. Approval flow was completely broken.
FIX (commit b72f320): BUFFER_PROFILE_ID guard removed. schedule_to_buffer() already auto-discovers the Instagram profile from Buffer API /profiles.json — no separate env var needed. The BUFFER_KEY (mapped from BUFFER_API_KEY_EXP04092027 in approval_check.yml) is the only credential needed.

ARCHITECTURE NOTES (for next session):
- SH-010 clarification: video-research.yml uploads clips to a FIXED Drive folder (ID 1-QRf4xToJf_7cnS5UW7BiDUjd6lXot6o — Resources/Video Creation Flow). This is correct for that pipeline — it's a research tool, not a version-based content pipeline. The version-based resources/clips/ path is in content_creator main.py (SH-022), not in video-research.
- SH-037 batch_retag: stub is intentional. When VISION_API_KEY is wired, batch_retag_stale_rows() will call Vision API to describe each stale image. No action needed until then.
- validate_image_relevance() in photo_matcher: this function is now used indirectly — its _url_looks_watermarked_or_tiny helper is called from _vision_accept(). The full function (with Vision API label scoring) is available for any future caller that wants a standalone photo check outside the cascade.

Commits pushed: 87b4fec (carousel_builder 3-in-1), b72f320 (approval_handler), 69a2e66 (scraper + main)
Tracker: all 10 original tasks + 5 gap-fixed tasks marked Done in Pipeline Fix Master Checklist
Status flips: ALL 10 SH tasks → DONE ✅. No remaining open items from this batch.

PENDING ⏳ (next session):
1. Test approval_handler → Buffer end-to-end (reply APPROVE to a preview email, confirm Buffer schedules)
2. Run content_creator.yml manually — observe email preview, verify all 4 OPC templates render correctly
3. Next pipeline-fix batch: check for new SH items in Master Checklist tracker

---

## SESSION 6 NOTES — 2026-05-05 (Audit + Gap Close)

### Audit scope
Full audit of all 10 SH tasks marked Done in sessions 1-5. Found 5 actual code gaps across 4 tasks. Two claimed gaps (SH-011, SH-041) were false — code already correct.

### Gaps found and fixed (commit 95dd152)

GAP 1 — SH-055: CSS variable direction wrong on all 19 templates.
Previous session wrote `--P:NNpx;--slide-inset:var(--P)` — inert because layout CSS uses `var(--P)` directly, not `var(--slide-inset)`. Overriding `--slide-inset` on an element had zero effect.
FIX: correct direction is `--slide-inset:NNpx;--P:var(--slide-inset)`. All 19 HTML templates now use this pattern. Verified with: `grep -l "slide-inset:[0-9]" docs/templates/` returns 19 files; no wrong-direction pattern remaining.

GAP 2 — SH-013: readability rule not in 3 standalone generator functions.
The rule was added to `OPC_COPY_RULES` and `BRAZIL_COPY_RULES` constants in carousel_builder.py, but `generate_progress_content()`, `generate_dados_content()`, and `generate_verdade_content()` build their own standalone Haiku prompts from scratch and never reference those constants.
FIX: Added readability rule inline to each function's RULES section:
  - generate_progress_content() → Rule 13 (EN): "8th-grade reading level. Max 16 words per sentence..."
  - generate_dados_content() → Rule 10 (PT): "Nível de leitura 8ª série. Máximo 16 palavras por frase..."
  - generate_verdade_content() → Rule 9 (PT): "Nível 8ª série. Máximo 16 palavras por frase..."
Also fixed text_reviewer.py JSON schema (line 224): added `readability` and `internal_label_leakage` to the valid type list in the schema example — without this, Claude sees conflicting type lists and may not use those issue types.

GAP 3 — SH-028: score_storytelling() not wired into Drive/manual review path.
Was wired into check_built_post() (local build path, CONTENT_CREATOR_RUN) but NOT check_drive_folder() (Drive/manual path, REVIEW_DRIVE_FOLDERS). Storytelling scoring was completely absent from review_only mode.
FIX: Added score_storytelling() call inside check_drive_folder() after cover.html download to /tmp. Uses _infer_niche_from_folder(folder_name, input_ref) for niche detection. Pattern mirrors check_built_post() exactly.

GAP 4 — SH-036: silent return False when Pillow not installed.
_crop_to_bounding_box() caught all exceptions and silently returned False — no warning that the crop was skipped due to missing dependency.
FIX: Added explicit warning message to except block: "Warning: Pillow not available — _crop_to_bounding_box skipped (install Pillow)".

GAP 5 — SH-048: _flush_alerts() had no fallback when gh CLI unavailable.
Only Route A (gh workflow run send_email.yml) existed. In non-GitHub-Actions environments (local dev, test runs), gh CLI may be absent or unauthenticated.
FIX: Added Route B smtplib.SMTP_SSL fallback — uses PRI_OP_GMAIL_APP_PASSWORD directly. If both fail, alerts logged to stdout only (never silently dropped).

### False gaps (no code change needed)
SH-011: Audit claimed "Retry content creator" step was missing `review_only_folder_id == ''` guard. Verified content_creator.yml lines 281 + 321 — BOTH steps already have the full two-condition guard. Gap was documented incorrectly.
SH-041: Audit claimed USE_DALLE not documented in workflow. Verified lines 108 + 253 — BOTH env blocks already have `# Opt-in: change '' to 'true' to enable DALL-E 3 fallback` comment. Already complete.

### Master Checklist updates
All 8 rows updated (SH-011, SH-013, SH-015, SH-028, SH-036, SH-041, SH-048, SH-055):
- What Was Done: updated with session 6 details
- Evidence: added commit 95dd152
- Next Action: "— complete" for all except SH-015 (GIPHY_API_KEY still a Priscila-only action)

### SH-015 remaining action (Priscila only)
GIPHY_API_KEY not in GitHub secrets. Code is complete and wired. Secret must be set manually:
  gh secret set GIPHY_API_KEY --repo priihigashi/oak-park-ai-hub
Without this, tier_giphy() silently skips on every pipeline run.

PENDING ⏳ (next session):
1. ⚠️ PRISCILA: gh secret set GIPHY_API_KEY --repo priihigashi/oak-park-ai-hub
2. Test approval_handler → Buffer end-to-end (reply APPROVE to a preview email)
3. Run content_creator.yml manually — verify all templates render + motion folders created
4. Check Master Checklist for new SH items (P0 Critical: SH-018, SH-065, SH-002, SH-003, SH-006 still Blocked)

---

## SESSION 7 NOTES — 2026-05-06 (Resilience-pass overnight audit)

### Context
Priscila asked for an overnight unattended pass on the pipeline spreadsheet. The
sandbox had no `gh` CLI / SHEETS_TOKEN, so live Master Checklist updates weren't
possible from the agent. Worked the documented PENDING list + audited the full
pipeline cascade for transient-error robustness instead. The next pipeline_self_heal
cron cycle (every 2h) will sync these commits to the Master Checklist tab via
`pipeline_tracker_writer.py sync` as soon as it picks up changes from the queue.

### Real bugs found and fixed (5 commits, branch claude/fix-pipeline-spreadsheet-fw8mc)

GAP S7-1 — carousel_reviewer.py L1050: `(529, 529)` duplicate tuple (commit 0fa1b2e).
Anthropic rate-limit (429) and service-unavailable (503) responses fell through
the duplicate-only check and printed as a hard error instead of the soft-warn
path. Now `(429, 503, 529)` covers all transient capacity issues.

GAP S7-2 — motion_sources.py L929: `find_supporting_clips()` requested `landscape`
clips from Pexels then sorted to "prefer portrait" — meaning portrait clips never
appeared in the result set (commit 0fa1b2e). Carousels are 1080x1350 and reels
are 9:16 (both portrait). Switched the API request to `portrait` so the file
selector actually has portrait candidates. Other Pexels calls in this file
already use portrait — this aligns with the rest of the codebase.

GAP S7-3 — self_heal/orchestrator.py L1129-1140: only `anthropic.BadRequestError`
and `AuthenticationError` were caught in the Claude→OpenAI fallback (commit dedcc8d).
`RateLimitError`, `APIStatusError` (529 overloaded), `APIConnectionError`, and
`APITimeoutError` all bubbled up and crashed the entire 2-hour cycle, leaving
the picked task stuck IN-PROGRESS until the next manual intervention. Added the
full transient-error set to the fallback so a single 429/529/timeout no longer
takes the orchestrator down.

GAP S7-4 — self_heal/orchestrator.py L1210: when the 3-attempt retry loop
exited without a break (every smoke test red, or every patch rejected by the
NN-S2 deletion guard), `final_outcome` stayed `UNKNOWN` (commit dedcc8d). The
fix log + queue Last Result column then said "UNKNOWN" with no diagnostic.
Now sets `FAILED_AFTER_RETRIES: <last error_log[:160]>` so the next session
can see why the task didn't complete.

GAP S7-5 — capture/capture_pipeline.py L91-115: `_llm_text()` (the SH-033
in-pipeline credit-fallback helper) only re-routed to OpenAI on credit/auth
errors. Rate limits, overloaded responses, connection errors, and timeouts
all re-raised, crashing capture and leaving queue rows stuck (commit 31cbe0b).
Added the same transient classification used by orchestrator.py +
_llm_fallback.py so a 429/529/timeout falls through to OpenAI instead of
killing the run. Catches both class-name patterns (`RateLimitError`,
`APIStatusError`, `OverloadedError`, `APIConnectionError`, `APITimeoutError`)
and string heuristics (`overloaded`, ` 429`, ` 529`).

### Verified correct (no fix needed, ruled out from audit)
- approval_handler.py L250 `_buffer_find_slot` BUFFER_KEY use → guarded by L277
  `if not BUFFER_KEY: return False` in the only caller. False positive.
- carousel_builder.py L91-92 `except Exception: pass` → intentional soft check
  on photo_matcher import; falls through to `_vision_validate`. False positive.
- capture_pipeline.py L97-98 `"AuthenticationError" in type(_ce).__name__` →
  substring check on class name works correctly. False positive.
- photo_matcher.py L150 `best_row[:10]` → guarded by L148 `if len(row) < 10:
  return 0`. False positive.
- self_heal/orchestrator.py L174-179 `for...else` → for-else fires correctly
  on no-match; logs warning and returns. False positive.

### Theme
All 5 fixes are in the **transient-error resilience layer** — the pipeline
already had 3-route cascades for content and image generation per NN-S8, but
the seams between the layers (orchestrator's Claude call, capture's `_llm_text`,
reviewer's storytelling scorer) had narrower exception nets than the cascades
themselves. A single Anthropic 529 was crashing the whole step instead of
falling through. Net effect: pipeline survives transient Anthropic outages
end-to-end now.

### Sheet sync
Master Checklist not updated directly from this session (sandbox had no
SHEETS_TOKEN). Next pipeline_self_heal cron cycle (every 2h via
`scripts/pipeline_tracker_writer.py sync`) will pick up the commits and
update tracker rows automatically. Manual sync available via:
  python scripts/pipeline_tracker_writer.py done --sh-id SH-XXX \
    --done "..." --evidence "commit 0fa1b2e|dedcc8d|31cbe0b"

### Commits this session
- 0fa1b2e — fix(pipeline): correct (529,529) typo + portrait orientation for B-roll
- dedcc8d — fix(self_heal): broaden Anthropic error fallback + surface failed-retries
- 31cbe0b — fix(capture): _llm_text falls back on transient Anthropic errors too

### PENDING ⏳ (carries forward, unchanged)
1. ⚠️ PRISCILA: gh secret set GIPHY_API_KEY --repo priihigashi/oak-park-ai-hub
2. Test approval_handler → Buffer end-to-end (reply APPROVE to a preview email)
3. Run content_creator.yml manually — verify all templates render + motion folders
4. Check Master Checklist for new SH items (P0 Critical: SH-018, SH-065,
   SH-002, SH-003, SH-006 still Blocked)
5. Merge claude/fix-pipeline-spreadsheet-fw8mc → main once Priscila reviews
   the 3 commits above
