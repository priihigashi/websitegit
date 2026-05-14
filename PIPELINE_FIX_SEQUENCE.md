# Pipeline Fix Sequence — Read This Before Every Session
# oak-park-ai-hub · 2026-05-01
# Follow this sequence every time. Do NOT skip steps.

---

## STRICT ORDER — EVERY SESSION

STEP 1 — FREEZE AUDIT
Do NOT edit any code until you complete this step.
- Read PIPELINE_REGISTRY.md to understand active templates
- Run: grep -rn "FIXME\|TODO\|HACK\|BROKEN\|SKIP" scripts/content_creator/ | head -20
- Run: cd ~/ClaudeWorkspace/oak-park-ai-hub && git log --oneline -10
- List what changed since last session and what is still in the fix queue below

STEP 2 — FIX ONLY POST-BLOCKING ISSUES
Fix in this order, one at a time, push after each:
1. caption.txt missing → check generate_caption() call in main.py
2. @HANDLE_PLACEHOLDER in HTML → check generate_dados_content() retry in carousel_builder.py
3. Stat slide text clips → check font-size + overflow in opc_tip_base.css
4. SWIPE overlaps list text → check padding-bottom in .list in opc_tip_base.css
5. Brazil FORMAT-019 built without capture brief → check brief gate in main.py before generate_dados_content()
After each fix: git add [file] → git commit → git push → verify on GitHub

STEP 3 — RUN 3 CONTROLLED TESTS
Run exactly ONE topic per niche:
- Brazil: gh workflow run content_creator.yml -f manual_mode=true -f manual_topic="[BRAZIL TOPIC]" -f manual_niche=brazil
- USA: gh workflow run content_creator.yml -f manual_mode=true -f manual_topic="[USA TOPIC]" -f manual_niche=usa
- OPC: gh workflow run content_creator.yml -f manual_mode=true -f manual_topic="[OPC TOPIC]" -f manual_niche=opc

For EACH run, verify (not just check GitHub status — check logs):
- gh run view [RUN_ID] --log | grep -i "error\|fail\|skip\|401\|403\|exception"
- Check 🚨 Pipeline Failures tab in Ideas & Inbox for new rows
- Open Drive version folder and confirm: png/ has slides, motion/ has MP4, caption.txt exists
- Report: reviewer result + Drive link + any issues

STEP 4 — FIX ONLY THE FAILING STAGE
If a test fails: fix ONLY the failing stage. Do not expand scope.
Commit, push, re-run the SAME topic to verify the fix.

STEP 5 — CONTENT QUALITY REVIEW
After reviewer passes, look at actual slides (download or view in Drive):
- Cover hook: has number or dollar or named consequence? If not → fix prompt in carousel_builder.py
- Images: match slide topic? If not → fix context_image_query instructions in Haiku prompt
- Label vs content: RED FLAG content has RED FLAG label? If not → fix label rule in prompt
- Caption: has hook + hashtags? If not → fix generate_caption() prompt

---

## WHAT DONE LOOKS LIKE

A session is done when:
✅ 3 test carousels ran and reviewer passed all 3
✅ Each test has: png/ + motion/ + caption.txt in Drive
✅ No rows in 🚨 Pipeline Failures tab from this session
✅ Commits pushed and GitHub shows green

---

## WHAT NOT TO DO

❌ Do NOT start a new test run before fixing the failing stage from the previous run
❌ Do NOT add new features while post-blocking bugs exist
❌ Do NOT rewrite working functions — only change what is strictly necessary
❌ Do NOT expand scope mid-session (new template, new niche, new feature)
❌ Do NOT report "done" based on GitHub green status alone — check logs + Drive

---

## PENDING FIXES QUEUE (update this after each session)

DONE (2026-05-01):
✅ Caption generation — generate_caption() defined + called in main.py
✅ caption.txt saved to Drive version folder
✅ SWIPE/list padding — padding-bottom:80px in opc_tip_base.css
✅ Stat overflow — overflow:hidden + clamp in opc_tip_base.css
✅ @HANDLE_PLACEHOLDER retry in generate_dados_content()
✅ FORMAT-019 brief gate — skip without capture_brief
✅ Cover hook formula in OPC Haiku prompt
✅ PRO MOVE → RED FLAG label rule in Haiku prompt
✅ Cream variant contrast overlay (FIX 9 in opc_tip_base.css)
✅ Brazil dark overlay on context-image slides (FIX 10 in carousel_builder.py)
✅ Reviewer: @HANDLE_PLACEHOLDER check added (2026-05-01)
✅ Reviewer: caption.txt existence check added (2026-05-01)
✅ PIPELINE_REGISTRY.md created — template source of truth

STILL PENDING:
⬜ Photo catalog 401 — real OPC photos never load (photo_matcher.py auth bug)
⬜ Per-slide image queries — all slides use same carousel-level query (IMG-03)
⬜ Build history tab — same topics can repeat after 30+ posts
🔄 Resource routing flow — notes/transcripts now emit resource_requests; next wire capture resource_router.py + resource_manifest.json
⬜ Clip pipeline dual output — video-research.yml doesn't write to resources/clips/ in version folders
⬜ Review-only mode in content_creator.yml (run reviewer on existing Drive folder)
⬜ Template organization in Marketing shared drive (visual template browser for Priscila)
⬜ NanoBanana credits — ⚠️ ONLY PRISCILA: nanobanana.com → add credits
⬜ Replicate credits — ⚠️ ONLY PRISCILA: replicate.com → Billing → add credits
⬜ Pexels API key — ⚠️ ONLY PRISCILA: pexels.com/api → regenerate → update PEXELS_API_KEY secret

---

## ONLY PRISCILA CAN DO (do these now so images work)

1. NanoBanana credits: go to nanobanana.com → account → add credits
   Without this: all person photos fall back to initials placeholder

2. Replicate credits: go to replicate.com → Billing → add credits
   Without this: SDXL image generation fails, falls back to DALL-E only

3. Pexels API key: go to pexels.com/api → copy new key → GitHub repo Settings → Secrets → update PEXELS_API_KEY
   Without this: all Pexels stock photo/video queries return 403, pipeline skips that tier
