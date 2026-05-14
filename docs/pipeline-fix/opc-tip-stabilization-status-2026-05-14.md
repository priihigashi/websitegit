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
- `84f4d28` — OPC hook payoff contract: strong promise hooks now require a visible answer in slide content.
- `25840775141` — static proof green; Hook 3/3, Coherence 2/3 purpose-aware.
- `25882369553` — hook payoff proof run; workflow success, Hook 3/3, Coherence 2/3, Reviewer 1/1, visual QA pass, motion skipped.
- `25841356531` / `25841356537` — Motion v2 Phase 1 manual proofs green; cover-only MP4.

## Still open
- Content quality pass in `carousel_builder.py` and source/fact discipline.
  - Target: Storytelling >=85/100 on one proof run.
  - Latest proof: Storytelling 70/100 from creator and 74/100 from reviewer on `25882369553`.
  - The hook answer is now present, but the carousel still reads like a weak comparison instead of a high-value story.
- Fact audit pass.
  - Latest proof: Fact Checker failed 5/10 on `25882369553`.
  - Do not approve the preview from Drive folder `1hzSFZO9zIZR144njyimP_EVo0VEf7C5y`.
- Visual approval.
  - Priscila must approve the preview email before Buffer scheduling.

## Do not start yet
- OPC template expansion.
- News pipeline proofing.

## Notes
- The prior claim that Pexels 403 currently causes placeholder slides is stale for recent proofs.
- Recent proof runs can still omit weak image slots when no candidate clears the matcher/vision threshold; this is preferable to placeholder boxes, but it can make slide 2 feel bare.
- Dirty local files from other work were not touched.
- Next safest fix is not more hook polish. It is one narrow patch for source-backed value: make cost/risk/stat claims in OPC comparisons either trace to retrieved evidence or fall back to safer, less specific wording.
