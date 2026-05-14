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
- OPC writer prompt now has a real hook/storytelling layer instead of only field-filling/legal constraints.

## Proof
- `0fe460d` — OPC cover hooks preserve numeric/cost hook.
- `f0b747b` — OPC middle-slide story labels replace generic labels.
- `367f737` — reviewer scores OPC coherence by slide purpose.
- `84f4d28` — OPC hook payoff contract: strong promise hooks now require a visible answer in slide content.
- `281342e` — OPC hook philosophy + full-circle story rule wired into writer prompt; reviewer now reports hook payoff fields.
- `25840775141` — static proof green; Hook 3/3, Coherence 2/3 purpose-aware.
- `25882369553` — hook payoff proof run; workflow success, Hook 3/3, Coherence 2/3, Reviewer 1/1, visual QA pass, motion skipped.
- `25841356531` / `25841356537` — Motion v2 Phase 1 manual proofs green; cover-only MP4.

## Still open
- Content quality proof after `281342e`.
  - Target: Storytelling >=85/100 on one proof run.
  - Latest proof before `281342e`: Storytelling 70/100 from creator and 74/100 from reviewer on `25882369553`.
  - Next proof must inspect the actual text first, then scores.
- Remaining writer prompt follow-up.
  - Do not redo `281342e`; build on it.
  - Add visible strategy fields to the OPC JSON output: `hook_frame`, `viewer_question`, `payoff`, `proof_needed`, `format_fit`, `needs_longer_format`, `visual_strategy`.
  - Add format-fit rule: use the selected template slide count, narrow broad topics, and mark `needs_longer_format=true` instead of cramming.
  - Add visual strategy rule: each middle-slide image should prove or clarify the story, not decorate.
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
- Next safest fix is the missing visible strategy/format-fit layer in `carousel_builder.py`, then one OPC proof. Keep News untouched until OPC Tip produces one postable carousel.
