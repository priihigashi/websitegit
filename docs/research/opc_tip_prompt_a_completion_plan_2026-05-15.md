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
