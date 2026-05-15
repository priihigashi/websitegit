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

## Errors / Gaps

- Gate 1 visual decision is not done. Priscila still needs to choose A, D, or adjust.
- Gate 2 cleanup is not fully done:
  - Kling is still present as explicit/manual-only legacy code, but workflow default is `KLING_APPROVE=0`.
  - The legacy `render_motion_cover()` helper still exists for historical reference, but active dispatch/fallback paths are blocked/removed.
  - Cron/prod motion is still off and must stay off.
- Gate 3 proof tests are not complete:
  - Fresh A proof needs final status and Drive link.
  - Fresh D proof needs final status and Drive link.
  - No-clip proof needs final status and Drive link.
  - Need to verify exactly one MP4 exists in `motion/`: `cream_01_cover_motion.mp4`.
  - Need to verify no `cream_02`, `cream_03`, or `cream_04` motion files.
- Gate 4 sign-off is blocked until Gates 1-3 pass.

## Current Gate Status

Gate 1 — Visual Decision: BLOCKED on Priscila review.

Gate 2 — Code Cleanup: PARTIAL.
- Cover-only bug fixed.
- No-clip switch added.
- Ken Burns active fallback removed; legacy manual route blocked.
- Kling default is off; explicit Kling path remains out of Phase 1.

Gate 3 — Remaining NN-M5 Tests: RUNNING / EVIDENCE PENDING.

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
