# OPC Tip Prompt A Completion Plan — 2026-05-15

## Goal

Finish Prompt A for the OPC tip carousel pipeline:

- Confirm the 16 verification checks pass.
- Build the concrete-block-vs-wood-framing carousel through `content_creator.yml`.
- Verify preview email, Drive PNGs, ethics/layout/storytelling gates.
- Reply `APPROVE` only after visual QA passes.
- Confirm `approval_handler.py` schedules the post to Buffer.

## Current State

Live repo: `priihigashi/oak-park-ai-hub`

Current `main`: `4969d6b`

Earlier audit target: `5e9f5c9`

Important correction from the combined audit:

- The original Check 7 was too structural. It confirmed `_apply_opc_hook_answer_contract()` existed, but it did not grep the literal fallback text.
- The fallback text still contained a banned resale-intent condition:
  `Go with {right} when budget today is the constraint and you plan to sell within 10 years.`
- That contradicted the ethics rule in the same file.

## Blockers

### Blocker 1 — `main.py` work-before-assignment

Status: Done before this plan was written.

Evidence:

- Commit `6cb4192` fixed the issue.
- Current location: `scripts/content_creator/main.py:1536`
- `work = WORK_DIR / post_id` now happens before the Clip Intelligence Layer reads `work/resources/...`.

Reference:

- `scripts/content_creator/main.py:1533-1544`

### Blocker 2 — banned resale fallback in builder

Status: Fixed in this session.

File:

- `scripts/content_creator/carousel_builder.py`

Function:

- `_apply_opc_hook_answer_contract()`

Old bad fallback:

```python
f"Go with {right} when budget today is the constraint and you plan to sell within 10 years."
```

New fallback:

```python
f"Go with {right} when the floor plan is complex, the timeline is tight, and interior finish flexibility is the priority."
```

Reference rules:

- `scripts/content_creator/carousel_builder.py:915-920` — comparison winner-condition rule.
- `scripts/content_creator/carousel_builder.py:922-928` — professional ethics rule.
- `scripts/content_creator/carousel_builder.py:1558` — `_apply_opc_hook_answer_contract()`.

## Required Verification

Run the 16-check verification gate before triggering the workflow.

Must verify:

- Closing callback rule exists.
- Winner-condition example uses humidity/hurricane/floor-plan language.
- `slide4_body` requires exactly 2 sentences and 260 characters.
- CTA bad list includes `SCREENSHOT BEFORE SIGNING ANYTHING`.
- `_apply_opc_hook_answer_contract()` contains no banned resale/sell-intent fallback.
- `check_opc_professional_ethics()` exists and scans resale phrases.
- `check_slide_layout_overflow()` exists and is wired.
- `closing_callback_found` is surfaced in storytelling output.
- CSS keeps Slide 3 subtext readable and context image slides unclipped.

Key files:

- `scripts/content_creator/carousel_builder.py`
- `scripts/content_creator/carousel_reviewer.py`
- `scripts/content_creator/opc_tip_base.css`

## Workflow Trigger

Use the exact Prompt A command:

```bash
~/bin/gh workflow run content_creator.yml --repo priihigashi/oak-park-ai-hub \
  -f manual_mode=true -f manual_niche=opc \
  -f manual_topic="concrete block vs wood framing — the cost most homeowners miss" \
  -f count_opc=1 -f manual_template=tip
```

## Log Audit

After the run finishes, inspect logs for:

```bash
SH-028
[ethics]
[layout]
closing_callback
SH-157
reviewer summary
ERROR
FAILED
```

Expected:

- No `[ethics]` issue.
- No `[layout]` overflow warning.
- Storytelling score at least 80.
- Closing callback found.

Reviewer references:

- `scripts/content_creator/carousel_reviewer.py:986` — `check_opc_professional_ethics()`
- `scripts/content_creator/carousel_reviewer.py:1309` — `check_drive_folder()`
- `scripts/content_creator/carousel_reviewer.py:1772` — `closing_callback_found`
- `scripts/content_creator/carousel_reviewer.py:2150` — `check_slide_layout_overflow()`
- `scripts/content_creator/carousel_reviewer.py:2175` — `check_built_post()`
- `scripts/content_creator/carousel_reviewer.py:2386` — `send_review_email()`

## Preview Email QA

Email destination:

- `priscila@oakpark-construction.com`

Check:

- Subject includes `concrete block vs wood framing`.
- Email says `Closing callback found: "..."`.
- Email does not say `CLOSING CALLBACK MISSING`.
- No ethics issue.
- No layout overflow warning.
- Storytelling score is at least 80.

## Drive Visual QA

Drive OPC carousel folder:

- `16P2JN74JAAW3HKnmNqPGPrAq7N5jDNii`

Open all 5 PNGs.

Check:

- Slide 3 subtext is readable.
- Slide 4 body is fully visible.
- Slide 4 final sentence uses structural, climate, code, or use-case criteria.
- Slide 4 does not mention resale, selling, flipping, or not staying long.
- Slide 5 CTA does not say `SCREENSHOT BEFORE SIGNING ANYTHING`.
- Closing callback resolves Slide 1 hook.

If any visual fails:

- Do not silently retry.
- File one issue with exact failed slide, failed rule, and next prompt-level fix.

If all pass:

- Reply `APPROVE` to the preview email.

## Approval / Buffer

Approval handler:

- `scripts/content_creator/approval_handler.py`

Important references:

- `scripts/content_creator/approval_handler.py:83` — `BUFFER_API_KEY`
- `scripts/content_creator/approval_handler.py:698` — Buffer expiry guard.
- `scripts/content_creator/approval_handler.py:757` — no Buffer key guard.

After approving:

- Confirm `approval_handler.py` schedules the post.
- Confirm the Buffer post exists.

## Deferred Prompt A.2 Debt

These do not block the first approved post, but should be fixed after Prompt A ships:

- Wire `check_opc_professional_ethics()` into `check_drive_folder()`.
- Wire `check_slide_layout_overflow()` into `check_drive_folder()`.
- Surface `closing_callback_found` explicitly in Drive-folder review output.

Reason:

The live Prompt A flow uses `check_built_post()`, but manual Drive-folder reviews should catch the same ethics/layout/closing-callback problems.

## Audit Lesson

Structural checks are not enough.

For any rule that bans a phrase or pattern, the verification gate must grep the literal banned content in addition to checking function wiring.

Example:

- Bad audit: `_apply_opc_hook_answer_contract()` exists and has comparison-pair logic.
- Correct audit: `_apply_opc_hook_answer_contract()` exists, has comparison-pair logic, and contains zero banned phrases such as `plan to sell`, `resale`, `not staying long`, or `flipping timeline`.

## Prompt A.1 Run Result

Run:

- https://github.com/priihigashi/oak-park-ai-hub/actions/runs/25938522662

Commit:

- `42790b3`

Result:

- Failed strict reviewer.

What passed:

- The old `work` crash did not recur.
- The 16 grep verification gate passed before the run.
- Storytelling passed with `SH-028 Storytelling overall=88/100`.
- No ethics failure appeared in the failed log.
- Slide 3 was visually readable.
- Slide 4 was visually fully visible and used structural/use-case language, not resale language.

What failed:

- Reviewer flagged: `Stat clipping risk: stat-big 1 is 22 chars ('UP TO $20K in 10 years').`
- Visual QA confirmed Slide 2 clips below the frame. The stat is too tall/long for the fixed stat slide layout, and bottom supporting text is cut off.
- Pre-email reviewer blocked preview sending: `[SH-157] No results passed pre-email reviewer — skipping send_preview`.

Drive evidence:

- Version folder: `https://drive.google.com/drive/folders/1SDfn5KJWjJNMb417Mvend7Fugb0n0iKh`
- PNG folder: `https://drive.google.com/drive/folders/1KERzc0MuEMftxNOAqH1hTIB6iGsnXV1_`
- Motion folder: `https://drive.google.com/drive/folders/1R_m1d5JZraQGQOTyp94TdHecuXNWD8YD`

Issue filed:

- https://github.com/priihigashi/oak-park-ai-hub/issues/151

Next prompt-level fix:

- Constrain OPC stat slide output so `stat-big` stays short enough for the fixed layout, or add responsive/dynamic sizing for long stat strings.
- For this exact content, split the stat into a shorter headline such as `$20K` and move `in 10 years` into supporting copy.
- Likely surfaces: `scripts/content_creator/carousel_builder.py` stat prompt/schema/fallback; `scripts/content_creator/opc_tip_base.css` `.stat-big` sizing/layout; `scripts/content_creator/carousel_reviewer.py` existing stat clipping threshold.

Approval status:

- Do not approve this post.
- No Buffer scheduling happened.

## Prompt A.2 — stat-clipping fix (in flight 2026-05-15 ~16:31 ET)

Fix shipped:

- Commit: `86fd041 fix(opc-tip): tighten slide2_stat to 18 chars to prevent clipping`
- Sits under: `f5bd143 feat(opc): PR0 — OPC_STANDALONE_ALLOWLIST per-template SH-158 bypass (#152)`
- File: `scripts/content_creator/carousel_builder.py` — line 509 (RULES block) + line 1368 (schema field)
- Change: `Max 40 chars` → `MAX 18 CHARS` with explicit GOOD/BAD examples and explicit ban on time-period qualifiers ("in 10 years", "over 5 years", "per year") in the stat field. Time/duration moves to slide2_label.
- Rationale: reviewer fires at >=22 char stats. 18-char limit gives 4-char buffer.
- Did NOT touch CSS or reviewer threshold — kept fix to one surface (Haiku prompt) for minimal blast radius.

Runs in flight on `f5bd143` (with the fix):

- 25939918327 — primary watch
- 25939935208 — secondary (same SHA)
- 25939892038 — on `709d5e0` (without fix, will fail)
- 25939847126 — on `709d5e0` (without fix, will fail)

Audit trail of all relevant runs:

- 25934460919 (5e9f5c9) — failed, original `work` UnboundLocalError
- 25936288580 (55aaa8b) — failed
- 25936639010 (6cb4192) — first SUCCESS (work-fix)
- 25938522662 (42790b3) — failed strict reviewer (stat clipping — caught the new blocker)
- 25939918327 (f5bd143) — IN FLIGHT, expected to pass

Pending after the run:

1. If pass — preview email lands in priscila@oakpark-construction.com — VISUAL QA per Drive Visual QA checklist above (slide 2 stat now `$20K` or `UP TO $20K`, no clipping).
2. If pass + visual OK — reply `APPROVE` to preview email. `approval_handler.py` schedules to Buffer.
3. If fail — pull `gh run view 25939918327 --log-failed`, identify whatever new blocker emerged, file in this section as Prompt A.3.

## 2026-05-17 19:50 ET — Buffer Retry Plan (Deep Audit)

Audited against origin/main `6624ee4`. Confirms Codex's `093006d` and corrects prior plan drift.

### Corrected facts (replacing prior drift)

- Final approved version folder: `1LyGQowJU2x48MrOGc_zP82peV2t2zCU_` — `v8_concrete-block-vs-wood-framing--the-cos` (NOT `13qr-...` from earlier note; that was an earlier iteration).
- Final PNG folder: `1aOCd3wY43g9KFsbDqMPXLyMiIk-vMesD`
- Final motion folder: `13kUkaKDnBN4HzctqHbnjX4aVH4IdT16U`
- All 3 folder IDs verified via Drive API 2026-05-17.
- `bbea9ce` is NOT a Prompt A commit — it is a capture/resource bridge change (Codex correctly flagged this).
- Prompt A scope is the SINGLE post `opc-tip-20260517-concrete-block-vs-wo`. Backlog scheduling is out of scope.

### Codex commit `093006d` — verified end-to-end

All claims real, file:line references:

- `.github/workflows/approval_check.yml:8-13` — new `retry_post_id` workflow_dispatch input.
- `.github/workflows/approval_check.yml:28` — wired into `RETRY_BUFFER_POST_ID` env var.
- `scripts/content_creator/approval_handler.py:21-22` — `BufferAuthError` class added.
- `scripts/content_creator/approval_handler.py:783-789` — 401 on `/profiles.json` raises BufferAuthError.
- `scripts/content_creator/approval_handler.py:852-857` — 401 on `/updates/create.json` raises BufferAuthError.
- `scripts/content_creator/approval_handler.py:1356,1370-1395` — `_get_pending_posts()` includes the `approved` row matching `RETRY_BUFFER_POST_ID`.
- `scripts/content_creator/approval_handler.py:1395` — emits `RETRY_BUFFER_POST_ID matched approved row: <post_id>` (grep marker).
- `scripts/content_creator/approval_handler.py:1657` — `buffer_failures` counter added to stats dict.
- `scripts/content_creator/approval_handler.py:1829` — `sys.exit(1)` when `buffer_failures > 0` (fixes silent-success bug).

### Pending — 3 steps, strictly ordered

Step 1 — ONLY Priscila can do this:

- Generate new Buffer API token at buffer.com → Settings → Apps.
- Update secret: `~/bin/gh secret set BUFFER_API_KEY_EXP04092027 --repo priihigashi/oak-park-ai-hub`
- Verify: `~/bin/gh secret list --repo priihigashi/oak-park-ai-hub | grep BUFFER` — timestamp should be 2026-05-17.

Step 2 — trigger retry workflow:

```bash
~/bin/gh workflow run approval_check.yml \
  --repo priihigashi/oak-park-ai-hub \
  -f retry_post_id="opc-tip-20260517-concrete-block-vs-wo"
```

Execution path:

1. Workflow `approval_check.yml:30` sets `RETRY_BUFFER_POST_ID` env.
2. `approval_handler.py:1356` reads it.
3. `_get_pending_posts()` includes the approved row.
4. `process_replies()` re-runs approve path → calls `schedule_to_buffer(variant, static_folder_id, caption)`.
5. Buffer `/profiles.json` succeeds with new token (no 401).
6. Buffer `/updates/create.json` schedules.
7. Logs print `Buffer scheduled OK: opc-tip-20260517-concrete-block-vs-wo (<variant>)`.
8. Script exits 0.

Step 3 — verify DONE (all 5 must be true):

1. `~/bin/gh run list --repo priihigashi/oak-park-ai-hub --workflow approval_check.yml --limit 1` → conclusion=success
2. Log grep: `RETRY_BUFFER_POST_ID matched approved row: opc-tip-20260517-concrete-block-vs-wo` (one match)
3. Log grep: `Buffer scheduled OK: opc-tip-20260517-concrete-block-vs-wo` (one match)
4. Buffer dashboard https://publish.buffer.com Instagram queue shows the carousel with 5 slides from folder `1aOCd3wY43g9KFsbDqMPXLyMiIk-vMesD` at a future scheduled time
5. Issue #154 closed: `~/bin/gh issue close 154 --repo priihigashi/oak-park-ai-hub --comment "Resolved — Buffer token renewed, post scheduled. See run <id>."`

### Known edge case (not a blocker for today)

`approval_handler.py:663` `_pick_target_posts` legacy fallback is `return pending[:1]`. If a new OPC `pending_approval` row enters the catalog between now and the retry, that row would be processed instead of the retry row. Currently extremely unlikely. Mitigation: verify catalog has no OPC pending rows before triggering Step 2.

### Out of scope for Prompt A

- Issue #156 (News pipeline audit) — DEFERRED until Prompt A ships.
- Catalog backlog scheduling (other approved-but-unscheduled posts) — separate cleanup, not Prompt A.
- Tech debt: catalog never transitions to "scheduled" — stays at "approved" forever. Re-running retry on already-scheduled post would schedule again. Log a `_scheduled` status migration for a future commit.

### Goal Done = post visible in Buffer Instagram queue with future scheduled time, AND issue #154 closed.

---

## Prompt A.2 RESULT — 2026-05-15 16:38 ET

Run 25939918327 (f5bd143 with my fix) completed in 6m55s — `conclusion: success`.

What passed:

- Stat clipping fix WORKED — no `Stat clipping risk` warning fired.
- Visual audit ✅ all slides have visual anchors.
- SH-146 contract ✅
- SH-149 Comparison parity ✅ — concrete block vs wood framing balanced.
- SH-155 HTML completeness gate ✅ — no empty fields, no visible placeholders.
- SH-159 post-render visual QA ✅ — 5 PNGs, family `['cream']`.
- SH-157 Pre-email reviewer ✅ 1/1 passed.
- Carousel reviewer ✅ Summary 1/1 passed.
- Preview email sent to priscila@oakpark-construction.com.

Drive artifacts:

- Version folder: https://drive.google.com/drive/folders/13qr-ixQcTF8qG4K4MeKHqWjRf6CfNp9u
- Motion folder: https://drive.google.com/drive/folders/1OkS0NDlwzxb07Mt_oWdPEuk5pLqXRky1

Soft warnings (advisory — did not block ship):

- SH-028 Storytelling JSON parse error twice (`Unterminated string` and `Expecting ',' delimiter`). Score was lost — `closing_callback_found` was effectively never validated. Non-fatal but means the closing-callback gate didn't actually run on this build. Followup: investigate Haiku JSON output stability for storytelling scorer.
- AI Content Audit (3 agents) — 0/1 passed all 3:
  - Fact Checker ❌ FAIL (no detail in log — see audit email)
  - Brand & Tone Reviewer ❌ FAIL (7/10)
  - Structure & Format ✅ PASS (8/10)
  - Detailed reasoning was emailed separately as "Audit report" to priscila@oakpark-construction.com.

What Priscila must do next:

1. Open the preview email — verify subject + storytelling section.
2. Open the audit email — read Fact Checker + Brand & Tone failure reasons.
3. Open Drive version folder + open all 5 PNGs.
4. Run the Drive Visual QA checklist above.
5. If everything looks correct + the audit failures are acceptable → reply `APPROVE` to preview email. If audit raised real issues → do NOT approve, file Prompt A.3.

This is the first build to reach the preview-email stage. Even if she rejects this specific build on content grounds, the pipeline itself is now end-to-end functional.

Deferred Prompt A debt (still applies — does NOT block first approved post):

- Wire `check_opc_professional_ethics()` into `check_drive_folder()` (carousel_reviewer.py:1309)
- Wire `check_slide_layout_overflow()` into `check_drive_folder()` (carousel_reviewer.py:1309)
- Surface `closing_callback_found` explicitly in Drive-folder review output (carousel_reviewer.py:1361)

These 3 cross-path gaps mean the manual Drive-folder review path is missing checks that the local CONTENT_CREATOR_RUN path has. Architect Aria flagged them in the audit. Bundle into one PR after Prompt A ships.

Audit lesson #2:

When operating in parallel with the user (she commits from her Mac while Claude commits via gh CLI), ALWAYS `git fetch + git pull --rebase` before push. Push was rejected once because she landed `709d5e0` while I was editing.

## Prompt A.3 — weak-source + stat-slide layout fix (2026-05-16/17)

Reason:

- Prompt A.2 reached preview email, but the content audit failed.
- The preview was not approved.

Blocking audit findings:

- Fact Checker failed because the carousel cited Angi/HomeAdvisor quote data as if it were audit-grade proof for repair-cost ranges.
- Fact Checker flagged `ACI 314.1R` as a bad/misattributed masonry source.
- Brand/Tone failed because Slide 3 had source/copy risk and the sourcing did not meet OPC rules.
- Visual QA also showed Slide 2's project note still sitting too close to/over the footer lane.

Fix shipped:

- Commit: `629e79c fix(opc-tip): block weak sources and tighten stat slide layout`

Files changed:

- `scripts/content_creator/carousel_builder.py`
- `scripts/content_creator/carousel_reviewer.py`
- `scripts/content_creator/opc_tip_base.css`

Changes:

- Strengthened OPC source prompt rules:
  - Angi/HomeAdvisor banned as primary proof for OPC numeric claims.
  - `ACI 314.1R` banned.
  - Masonry/concrete structural criteria must use TMS 402/602 or ACI 530/ASCE 5 only when relevant.
  - UF IFAS allowed for termite/wood-destroying organism risk.
  - FBC/IRC allowed for code minimums, not cost estimates.
  - NAHB allowed for construction cost category shares, not exact concrete-vs-wood repair ranges.
- Tightened `slide2_label` to max 95 characters.
- Tightened `slide3_items[].sub` to max 115 characters and told the model to avoid unsupported dollar ranges.
- Added reviewer gate for banned OPC source patterns:
  - `ACI 314.1R`
  - `Angi`
  - `HomeAdvisor`
- Tightened stat-slide CSS so the note and label stay out of the footer/legal lane.

Verification before rerun:

- `python3 -m py_compile scripts/content_creator/carousel_builder.py scripts/content_creator/carousel_reviewer.py` passed.
- Original 16 Prompt A grep checks passed: `16/16`.

Run in flight:

- https://github.com/priihigashi/oak-park-ai-hub/actions/runs/25979995564

Next:

- If the run passes, inspect preview email + audit email + all 5 PNGs.
- Do not approve unless content audit passes or failures are clearly false positives.
- If visual/content QA passes, reply `APPROVE` to the preview email and confirm Buffer scheduling.

## Prompt A.4 — final Slide 4 sentence-count escape (2026-05-17)

Reason:

- Run `25980560036` succeeded and the AI content audit passed, but visual QA still showed Slide 4 rendering as four sentences.
- The original Prompt A rule requires `slide4_body` to be exactly 2 sentences and max 260 characters.
- The existing normalization in `_apply_opc_hook_answer_contract()` only fired before `_comparison_pair` was attached, so comparison topics could still render model-written 4-sentence copy.

Blocking visual finding:

- Drive folder: `1s8tJ7BGOwv_ivxrRpRS8ELdlmTqUdxf7`
- Slide 4 text rendered:
  - `Ask your contractor about the maintenance history of homes in your area before choosing framing material. Wood might seem cheaper now, but termite damage can cost up to $15K by year 10. Go with concrete block when durability is key. Choose wood framing when initial cost savings and flexibility matter more.`
- This avoided resale language, but it violated the hard 2-sentence rule.

Fix being shipped:

- File: `scripts/content_creator/carousel_builder.py`
- Function: `enforce_opc_comparison_parity(content, topic, brief="")`
- Section: immediately after `_comparison_pair` is attached and before media-query enforcement.
- Behavior: if a comparison topic has a non-empty `slide4_body` and the sentence count is not exactly 2, replace it with:
  - `Go with {left} when long-term durability and lower maintenance matter. Go with {right} when the floor plan is complex and interior finish flexibility is the priority.`

Verification before rerun:

- `python3 -m py_compile scripts/content_creator/carousel_builder.py scripts/content_creator/carousel_reviewer.py` passed.
- Direct function test passed:
  - input: the 4-sentence Slide 4 body from run `25980560036`
  - output: exactly 2 sentences
  - pair: `{"left": "concrete block", "right": "wood framing"}`
- Original Prompt A grep gate passed: `16/16`.

Next:

- Commit + push this one-file fix.
- Re-run Prompt A workflow with the exact original topic and template.
- Inspect logs for `SH-028`, `[ethics]`, `[layout]`, `closing_callback`, and reviewer summary.
- Open the latest preview email and confirm:
  - subject includes the carousel topic
  - `Closing callback found: "..."`
  - no ethics issue
  - no layout warning
  - storytelling score is at least 80
- Open all 5 PNGs in Drive and approve only if:
  - Slide 3 sub-text is readable
  - Slide 4 is fully visible and exactly 2 sentences
  - Slide 4 uses climate/code/structural criteria, not resale intent
  - Slide 5 CTA is not `SCREENSHOT BEFORE SIGNING ANYTHING`
- If all pass, reply `APPROVE` to the preview email and confirm Buffer scheduling.

## Prompt A.5 — preview email closing-callback surfacing (2026-05-17)

Reason:

- Run `25980751261` succeeded, visual QA passed, and the preview email story score was `86/100`.
- The preview email still did not contain the required literal line: `Closing callback found: "..."`.
- Prompt A requires that line before approval, so v7 was not approved even though the carousel itself looked acceptable.

Fix being shipped:

- File: `scripts/content_creator/email_preview.py`
- Function: `_build_one_carousel_html(post, slides)`
- Section: Story Quality block, using `post["_storytelling_scores"]`.
- Behavior:
  - if `closing_callback_found is True`, show `Closing callback found: "{closing_callback_text}"`
  - if `closing_callback_found is False`, show `CLOSING CALLBACK MISSING`
  - otherwise show `Closing callback found: unknown`

Verification before rerun:

- `python3 -m py_compile scripts/content_creator/email_preview.py scripts/content_creator/carousel_builder.py scripts/content_creator/carousel_reviewer.py` passed.
- Direct HTML render test passed:
  - includes `Closing callback found:`
  - does not include `CLOSING CALLBACK MISSING` when callback is true

Next:

- Commit + push this email-only surfacing fix.
- Re-run the exact Prompt A workflow.
- Open the latest preview email and confirm the callback line appears.
- If visual QA still passes and preview score is at least 80, reply `APPROVE` and confirm Buffer scheduling.

## Prompt A final state — content approved, Buffer blocked (2026-05-17)

Completed:

- Final content run succeeded: `25980913663`
- Final approval run succeeded mechanically: `25981041442`
- Preview email: `19e3421029d1e7f6`
- Approval reply sent: `19e3422a0c443b9d`
- Final Drive version folder: `1LyGQowJU2x48MrOGc_zP82peV2t2zCU_`
- Final PNG folder: `1aOCd3wY43g9KFsbDqMPXLyMiIk-vMesD`
- Motion folder: `13kUkaKDnBN4HzctqHbnjX4aVH4IdT16U`
- Prompt A content/layout issue closed: GitHub issue `#151`

Final checks:

- Preview email score: `86/100`
- Preview email contains `Closing callback found: "..."`
- Post-build reviewer score: `85/100`
- Reviewer summary: `1/1 passed`
- AI Content Audit: Fact Checker PASS, Brand & Tone PASS, Structure & Format PASS
- Visual QA passed:
  - Slide 2 stat fully visible
  - Slide 3 sub-text readable
  - Slide 4 exactly 2 sentences, fully visible, and uses structural criteria
  - Slide 4 has no resale / sell-intent language
  - Slide 5 CTA is `SHOW THIS TO YOUR CONTRACTOR FIRST.`

Blocked:

- Buffer scheduling failed in `approval_check.yml` run `25981041442`.
- Log evidence:
  - `Reply: 'APPROVE' → approve (targets=1)`
  - `Buffer profiles error: HTTP Error 401: Unauthorized`
  - `Pipeline failure logged [buffer_schedule]: schedule_to_buffer returned False`
  - `Catalog: opc-tip-20260517-concrete-block-vs-wo → approved`

Open blocker:

- GitHub issue `#154`: Buffer scheduling blocked after Prompt A approval: 401 Unauthorized.

Only remaining task:

- Renew/regenerate the Buffer API token and update GitHub secret `BUFFER_API_KEY_EXP04092027`.
- Then rerun `approval_check.yml` with the narrow retry input for `opc-tip-20260517-concrete-block-vs-wo`.
- Done condition: approval handler logs `Buffer scheduled OK: opc-tip-20260517-concrete-block-vs-wo` and the post exists in Buffer.

## Prompt A scheduling retry patch (2026-05-17)

Reason:

- After the failed Buffer attempt, `approval_handler.py` marked the Prompt A catalog row as `approved`.
- A plain rerun of `approval_check.yml` would not pick that post up again because normal selection reads only `pending_approval` rows.
- The workflow also exited green even when Buffer scheduling failed, because the failure was logged but not reflected in the process exit code.

Fix shipped:

- Commit: `093006d fix(approval): fail on Buffer schedule errors and add post retry input`
- File: `.github/workflows/approval_check.yml`
  - added workflow_dispatch input `retry_post_id`
  - exports it as `RETRY_BUFFER_POST_ID`
- File: `scripts/content_creator/approval_handler.py`
  - if `RETRY_BUFFER_POST_ID` matches an already-approved row, include that row for one scheduling retry
  - Buffer HTTP 401 now raises a clear auth error that says to renew `BUFFER_API_KEY_EXP04092027`
  - Buffer scheduling failures increment `buffer_failures`
  - `__main__` exits non-zero when `buffer_failures > 0`, so GitHub Actions no longer shows fake success

Verified:

- `python3 -m py_compile scripts/content_creator/approval_handler.py` passed.
- Local retry selector test with `RETRY_BUFFER_POST_ID=opc-tip-20260517-concrete-block-vs-wo` found the approved row and final Drive folders:
  - static: `1LyGQowJU2x48MrOGc_zP82peV2t2zCU_`
  - motion: `13kUkaKDnBN4HzctqHbnjX4aVH4IdT16U`

Correct rerun command after Buffer token is renewed:

```bash
~/bin/gh workflow run approval_check.yml --repo priihigashi/oak-park-ai-hub \
  -f retry_post_id="opc-tip-20260517-concrete-block-vs-wo"
```

Done condition:

- workflow conclusion is success
- log contains `RETRY_BUFFER_POST_ID matched approved row: opc-tip-20260517-concrete-block-vs-wo`
- log contains `Buffer scheduled OK: opc-tip-20260517-concrete-block-vs-wo`
- Buffer dashboard shows the Instagram carousel scheduled

## News follow-up captured (2026-05-17)

Priscila flagged a separate News issue: chosen News items/templates/assets are not being used downstream.

Tracking issue:

- `#156` — Audit News pipeline: chosen items/templates are not being used downstream.

Scope:

- Do not mix with Prompt A.
- After Buffer is unblocked, audit News rows/items that were chosen or approved but not used.
- Trace through `content_creator.yml`, `main.py`, `carousel_builder.py`, `approval_handler.py`, and any News template chooser logic.
- Produce one concrete stuck News example before patching.
