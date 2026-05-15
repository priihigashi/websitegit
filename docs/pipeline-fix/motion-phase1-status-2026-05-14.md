# Motion Phase 1 Status — 2026-05-14

Status: IN PROGRESS — do not mark complete yet.

## Goal

Complete Motion System v2 Phase 1 for OPC carousel covers only.

Phase 1 means:
- Playwright/HTML recording only.
- Cover slide only.
- Layout A and Layout D proof outputs.
- No Remotion.
- No Kling.
- No Ken Burns fallback in Phase 1.
- Cron/prod motion remains off.

## Commits Shipped

- `34944b3` — Batch 2: add `motion_test` + `motion_cover_layout` manual workflow inputs.
- `464cacb` — Motion v2: isolate Phase 1 Playwright tests.
- `fb6948a` — Motion v2: enforce cover-only Phase 1 proofs.
- `9459c40` — Motion v2: add manual no-clip proof switch.

Related nearby commits:
- `464a58c` — M-001/M-002: remove KB zoom from motion HTML, add Layout A/B/D routing skeleton.
- `367f737` — Reviewer: score OPC coherence by slide purpose.

## What Is Done

- Manual workflow input `motion_test=1` exists.
- Manual workflow input `motion_cover_layout=A|D` exists.
- Manual workflow input `motion_force_no_clip=1` exists for the no-clip proof.
- Motion System v2 spec is locked in `NONNEGOTIABLES.md` by commit `190003c`.
- Spec-alignment patch shipped by commit `374f575` on 2026-05-14:
  - active Ken Burns fallback removed from `process_one_topic()`.
  - legacy `MANUAL_TEMPLATE=motion` path blocked so ffmpeg full-PNG zoom cannot run.
  - workflow UI labels `motion` as disabled by NN-M1.
  - `motion_sources.py` active chain is now real clips → GIPHY → static PNG/no motion.
  - old prompt/docstrings no longer tell the model to use Ken Burns as the last resort.
- Phase 1 guard logs when enabled:
  - `Motion Phase 1 Test: ON`
  - `Renderer: Playwright only`
  - `Cover layout: A|D`
  - `Remotion/Kling/Ken Burns: skipped by Phase 1 guard`
- Phase 1 now fetches cover clip only.
- Phase 1 now builds cover motion HTML only.
- Phase 1 now records cover only.
- `record_motion.js` waits for font readiness and video readiness instead of a blind 800ms wait.
- `motion_sources.py` uses `giphy_query` before generic stock/video queries.

## Proof Runs

Initial proof pair, before cover-only fix:
- Cover A: `25840417940` — success.
  - Drive: https://drive.google.com/drive/folders/1mZKJepHBYuoHzmk0YgzmpCMmqwqmUEUa
  - Found issue: generated non-cover motion files including tiny `cream_04` (~13KB).
- Cover D: `25840417955` — success.
  - Drive: https://drive.google.com/drive/folders/12GRxtJyO3QxWzfmS0cwYJCYVJqI_iCEP
  - Found issue: generated non-cover motion files including tiny `cream_04` (~14KB).

Fresh proof runs after cover-only fix:
- Cover A: `25841356531` — IN PROGRESS at last check.
- Cover D: `25841356537` — IN PROGRESS at last check.
- No-clip fallback: `25841475772` — IN PROGRESS at last check.

Do not use the initial proof pair as final NN-M5 evidence because those runs exposed the cover-only bug.

Spec-aligned proof attempt, 2026-05-15:
- Cover A: `25926538426` — produced cover-only proof before late cancellation request.
  - Version: https://drive.google.com/drive/folders/12mbp4Y5fybQgRHwhZeZAsHTH4sdy6Bgq
  - Motion: https://drive.google.com/drive/folders/1xHxeTzNFxuX5YWMd_nZyPV7s-W_1CsyZ
  - Drive folder contains one MP4: `cream_01_cover_motion.mp4`.
- Cover D: `25926538378` — produced cover-only proofs before late cancellation request.
  - Version 1: https://drive.google.com/drive/folders/14bxRFHuQMiWLObjQRcIoq1F7fLwMBBoT
  - Motion 1: https://drive.google.com/drive/folders/10D1PB17oIj4Z5F-_Om9KpYEvrlt1gOdx
  - Version 2: https://drive.google.com/drive/folders/1YBomEqBIL0xLmhFE38QDhxJdUq8jwQ2D
  - Motion 2: https://drive.google.com/drive/folders/1IIINGMBzf2si6CtEctWo7YrrcFtSmRtw
  - Each Drive motion folder contains one MP4: `cream_01_cover_motion.mp4`.
- No-clip: `25926538324` — FAILED NN-M5 before fix.
  - Version: https://drive.google.com/drive/folders/140DTwYBuTTKOtDno4cTW9p5-fpp4RCir
  - Motion: https://drive.google.com/drive/folders/1SAWGHWRsxFKMUO7_Ufjzu0TmUOHQZykj
  - Failure: `MOTION_FORCE_NO_CLIP=1` still produced `cream_01_cover_motion.mp4`.
- No-clip fix shipped:
  - `e62b179` — Motion v2: make Phase 1 no-clip proof static-only.
- No-clip rerun:
  - `25927210711` — cancelled because the workflow stayed stale in `Run content creator`.
  - Status: no clean no-clip production proof yet after `e62b179`.

## Errors / Gaps

- Gate 1 visual decision is not done. Priscila still needs to choose A, D, or adjust.
- Gate 2 cleanup is not fully done:
  - Kling is still present as explicit/manual-only legacy code, but workflow default is `KLING_APPROVE=0`.
  - The legacy `render_motion_cover()` helper still exists for historical reference, but active dispatch/fallback paths are blocked/removed.
  - Cron/prod motion is still off and must stay off.
- Gate 3 proof tests are not complete:
  - Cover A/D produced usable Drive proofs, but runs were marked cancelled after proof output due late cancellation request.
  - No-clip proof needs one clean successful workflow run after `e62b179`.
  - A/D Drive folders verified exactly one MP4 exists in `motion/`: `cream_01_cover_motion.mp4`.
  - A/D Drive folders verified no `cream_02`, `cream_03`, or `cream_04` motion MP4 files.
- Gate 4 sign-off is blocked until Gates 1-3 pass.

## Current Gate Status

## 2026-05-15 Cover D Visual Stabilization Update

Status remains: IN PROGRESS — Cover D is not approved yet.

Priscila's Cover D run 1 complaints preserved as open visual criteria:
- content was concentrated too high on the slide.
- title needed to be larger with cleaner three-line breaks.
- right-side motion rectangle position was wrong.
- subhead was barely readable.
- MP4/GIF quality made text feel degraded.
- loop flashed deep black.
- GIPHY returned time-lapse / unrelated-feeling motion.
- cover photo kept repeating the same kitchen image for unrelated topics.
- middle-slide motion placeholder formats are still Phase 2.
- full-bleed slow ambient background without a rectangle is still Phase 2 / later.

Shipped fixes:
- `55aaa8b` — Cover D layout, subhead readability, right-side frame position, loop poster, initial photo-query and GIPHY quality work.
- `6cb4192` — fixed workflow crash: create `work` dir before clip intelligence lookup.
- `00c8d9c` — real fix for repeated kitchen cover bug: prefer workflow topic before generated headline in OPC cover photo matching.
- `7387bc0` — prevent clipped Cover D title and reject unrelated GIPHY clips.

Fresh proof evidence after latest fixes:
- Run `25937372249` — SUCCESS on commit `7387bc0`.
- Version: https://drive.google.com/drive/folders/1u0qmn51nQC7l1cwqMwj9MC4DpKzkDbwB
- Motion: https://drive.google.com/drive/folders/1YzQCtFnp16sGuF60Jw3WlyoLYqRrZSko
- Log proof:
  - `MOTION_ENABLED=1`
  - `MOTION_PHASE1_TEST=1`
  - `MOTION_COVER_LAYOUT=D`
  - `KLING_APPROVE=0`
  - `Motion Phase 1 Test: ON`
  - `Renderer: Playwright only`
  - `Remotion/Kling/Ken Burns: skipped by Phase 1 guard`
  - `Playwright recorded: 1 MP4(s) (slides [1])`
  - cover photo matched `Bathroom remodel planning` → `IMG_6595.jpeg (Bathrooms)`.
- Drive motion folder contains one MP4 only: `cream_01_cover_motion.mp4`.
- Static PNGs for slides 2-5 are present; no middle-slide motion MP4s were produced.

Known remaining visual review risk:
- Priscila still needs to watch the v4 MP4 and decide OK vs adjust.
- Text-quality/bitrate is not separately fixed; judge visually from the v4 output.
- GIPHY relevance is improved by metadata filtering, but Priscila must decide whether the selected clip feels right.

Gate 1 — Visual Decision: BLOCKED on Priscila review.

Gate 2 — Code Cleanup: PARTIAL.
- Cover-only bug fixed.
- No-clip switch added.
- Ken Burns active fallback removed; legacy manual route blocked.
- Kling default is off; explicit Kling path remains out of Phase 1.

Gate 3 — Remaining NN-M5 Tests: RUNNING / EVIDENCE PENDING.
Gate 3 update — 2026-05-15: A/D proof artifacts exist; no-clip still evidence-pending after fix `e62b179`.

Gate 4 — Phase 1 Sign-Off: BLOCKED.

## Next Actions

1. Wait for runs `25841356531`, `25841356537`, and `25841475772` to complete.
2. After the spec-alignment patch is pushed, run fresh proof pair again if needed:
   - Cover A with `motion_test=1`, `motion_cover_layout=A`.
   - Cover D with `motion_test=1`, `motion_cover_layout=D`.
   - No-clip with `motion_test=1`, `motion_force_no_clip=1`.
3. Inspect logs for:
   - `Phase 1 proof mode — cover clip only`
   - `cover motion HTML only`
   - `Phase 1 proof scope: recording cover only`
   - exactly one `Motion recorded: cream_01_cover_motion.mp4`
4. Inspect Drive `motion/` folders:
   - confirm only cover MP4 is present.
   - confirm no orphan middle-slide MP4s.
5. Priscila reviews cover MP4s and chooses A, D, or adjust.
6. Only after proof evidence and visual decision: update NN-M5 with Drive links and mark Phase 1 complete.

## Do Not Do Yet

- Do not set `MOTION_ENABLED=1` for cron/prod.
- Do not touch `scripts/remotion/src/CarouselMotion.tsx`.
- Do not mark M-001 through M-006 all Done.
- Do not mark Motion Phase 1 complete.
- Do not expand motion to non-cover slides.
- Do not schedule/post motion output to Buffer.
