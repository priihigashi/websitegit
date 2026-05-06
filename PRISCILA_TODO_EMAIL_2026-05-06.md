# Priscila-only items — paste into send_email.yml

Trigger this email manually:
- GitHub → Actions → Send Email (on demand) → Run workflow
- to: `priscila@oakpark-construction.com`
- subject: `Pipeline — 5 things only YOU can do (2026-05-06)`
- body: paste the block below

OR run from terminal:
```
gh workflow run send_email.yml --repo priihigashi/oak-park-ai-hub \
  -f to=priscila@oakpark-construction.com \
  -f subject="Pipeline — 5 things only YOU can do (2026-05-06)" \
  -f body="$(cat <<'EOF'
[paste body below]
EOF
)"
```

---

## SUBJECT
Pipeline — 5 things only YOU can do (2026-05-06)

## BODY

Hi Priscila — overnight pass on the pipeline-fix branch is done. Here are
the items I cannot do from the sandbox (no gh CLI / no live Sheets API).

5 ITEMS TO ACTION (priority order):

1) GIPHY_API_KEY — set the GitHub secret
   Why: tier_giphy() in motion_sources.py silently skips every pipeline run
   without it. Code is wired and ready.
   How: gh secret set GIPHY_API_KEY --repo priihigashi/oak-park-ai-hub
   Source: giphy.com/developers → My Apps → Beta Key (free tier, 100 req/hr)

2) NanoBanana credits — add credits at nanobanana.com
   Why: every person photo in news content falls back to bio-initials
   placeholder when NB2 is out of credits. AI-cascade tier 1 dies silently.
   How: nanobanana.com → log in → add credits ($10 covers ~100 images)

3) Replicate credits — add credits at replicate.com
   Why: SDXL image fallback (tier 6 of OPC, tier 3 of news) fails. Pipeline
   then falls through to DALL-E only or to placeholder.
   How: replicate.com → Billing → top up

4) Pexels API key — regenerate if expired
   Why: only re-do this one IF you see "Pexels 403 forbidden" in pipeline
   logs. PEXELS_API_KEY is set per the SH-022 verification, but key
   rotation cycles invalidate it periodically.
   How: pexels.com/api → regenerate → update PEXELS_API_KEY secret

5) Test approval_handler → Buffer end-to-end
   Why: SH-029 removed the BUFFER_PROFILE_ID guard so this should work,
   but it has not been tested with a real APPROVE reply yet.
   How: wait for the next preview email from content_creator.yml, reply
   "APPROVE" or "black approved", confirm Buffer schedules within 5 min.

PIPELINE STATUS
- Branch claude/fix-pipeline-spreadsheet-fw8mc has 4 new commits
  (0fa1b2e, dedcc8d, 31cbe0b, a47b172). All 5 fixes are transient-error
  resilience patches — pipeline now survives Anthropic 429/529 outages
  end-to-end. Merge to main when you're ready.

- Master Checklist sheet will auto-sync via the next pipeline_self_heal
  cron (every 2h via scripts/pipeline_tracker_writer.py sync). No manual
  sheet update needed from your side.

- P0 Blocked items in the Master Checklist (SH-018, SH-065, SH-002,
  SH-003, SH-006) need their queue rows populated with target_file +
  verification_method before the orchestrator can pick them up. I
  couldn't act on these without sheet read access from the sandbox.

— Claude (overnight session 7)
