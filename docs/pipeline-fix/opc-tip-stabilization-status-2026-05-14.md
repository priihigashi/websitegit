# OPC Tip Stabilization Status — 2026-05-14

## Current goal
OPC Tip Pipeline stabilization is not closed yet.

## Done
- Static render/title path is green.
- Motion stays disabled on normal static runs (`MOTION_ENABLED=0`).
- Motion v2 Phase 1 manual proofs are scoped to `MOTION_PHASE1_TEST=1`, cover-only.
- Smart Slide Picker cutover is already done:
  - Workflow default is `OPC_SLIDE_PLANNER_ENABLED=1`.
  - Master Checklist row 84 is already `Done`.
  - Registry standalones are marked `production_wired`.
- Reviewer now scores OPC coherence by slide purpose.

## Proof
- `0fe460d` — OPC cover hooks preserve numeric/cost hook.
- `f0b747b` — OPC middle-slide story labels replace generic labels.
- `367f737` — reviewer scores OPC coherence by slide purpose.
- `25840775141` — static proof green; Hook 3/3, Coherence 2/3 purpose-aware.
- `25841356531` / `25841356537` — Motion v2 Phase 1 manual proofs green; cover-only MP4.

## Still open
- Content quality pass in `carousel_builder.py`.
  - Target: Storytelling >=85/100 on one proof run.
  - Current range: about 75-80/100.
- Visual approval.
  - Priscila must approve the preview email before Buffer scheduling.

## Do not start yet
- OPC template expansion.
- News pipeline proofing.

## Notes
- The prior claim that Pexels 403 currently causes placeholder slides is stale for recent proofs.
- Recent proof runs used real images and passed visual QA.
- Dirty local files from other work were not touched.
