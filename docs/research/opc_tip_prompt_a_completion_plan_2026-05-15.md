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
