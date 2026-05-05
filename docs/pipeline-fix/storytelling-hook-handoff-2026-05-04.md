# Storytelling + Hook Quality Handoff — 2026-05-04

Related issue: https://github.com/priihigashi/oak-park-ai-hub/issues/122

## Current status

This handoff documents the content/storytelling work from the chat about Lau’s content-creation workflow, OPC content, News content, carousel templates, and hook quality.

Important: this is a partial implementation.

Completed:
- Created and documented GitHub Issue #122.
- Added detailed handoff comments to Issue #122.
- Added hook excellence notes to Issue #122.
- Patched `scripts/content_creator/text_reviewer.py` to add a reviewer-level hook/storytelling quality gate.
- Verified after patch that `HOOK_STORYTELLING_REVIEW_RULES` exists in `text_reviewer.py` on `main`.

Not completed yet:
- `scripts/content_creator/carousel_builder.py` generation prompts/schema are not patched yet.
- Capture/content brief generation is not patched yet.
- No OPC + News sample generation has been run after the reviewer patch.
- No full end-to-end GitHub workflow verification has been completed.

Commit already made:
- `b2428b4ecdd33553213d6ebb6494d34e7036a0c8`
- File changed: `scripts/content_creator/text_reviewer.py`
- Purpose: enforce hook and storytelling quality in the reviewer.

## Why this task exists

Priscila reviewed Lau’s content-creation video and wanted to extract useful workflow principles without incorrectly changing OPC or News content style.

The core lesson from Lau is:
- start from a central point;
- branch into supporting points;
- research more when needed;
- compress the information;
- stitch the facts into one clear explanation.

But this must not turn News posts into behind-the-scenes verification posts.

## Critical correction from Priscila

Do not misunderstand Lau’s process video.

Lau’s specific video was a meta/content-process post, so that video’s hook could be about research organization.

But Lau’s normal news posts are not necessarily about how she researched. They show the news/source/article/context.

Therefore, for our pipeline:
- News posts should not default to “how we verified this.”
- News posts should verify behind the scenes and show receipts/sources clearly.
- Sources/receipts can include article screenshots, website screenshots, official docs, timelines, law text, source excerpts, or multiple-source confirmation.
- Behind-the-scenes verification should only be used when the format explicitly asks for a process/behind-the-scenes post.

## Hook rule — very important

Priscila specifically corrected that hooks are critical and must not be average.

Do not weaken hooks.

Hooks must remain strong, audience-facing, and niche-specific.

Bad/generic hooks to reject:
- “Here’s what you need to know”
- “Let’s talk about”
- “Important update”
- “You won’t believe this”
- “Things homeowners should know”
- “What happened today”
- “Tips and tricks”
- “What to do”

Good hook patterns:
- Specific number that demands explanation.
- Contradiction or tension.
- Consequence hook.
- Question with stakes.
- Source/receipt hook.
- Curiosity gap without deception.
- Visual proof / before-after hook.

## Niche-specific hook standards

### OPC

OPC hooks should be homeowner-facing and business-safe.

Use:
- homeowner risk;
- cost risk;
- delay risk;
- hidden consequence;
- missing scope;
- permit/code issue;
- bad quote comparison;
- material/process misunderstanding;
- what to ask before signing.

Avoid:
- promises about OPC;
- “we always” language;
- exact numbers without qualifiers;
- scare tactics;
- sales copy;
- generic homeowner tips.

Example hook directions:
- “The cheapest quote can become the most expensive project.”
- “One missing scope line can change the whole budget.”
- “The expensive part is not always the finish.”

### News Brazil / USA

News hooks should be journalistic and credible but still intriguing.

Use:
- exact public/viral claim;
- contradiction;
- vote/result;
- official source/document;
- legal consequence;
- institutional tension;
- missing context;
- confirmed vs unproven;
- number with consequence.

Avoid:
- clickbait;
- partisan tone;
- pretending something is proven when it is not;
- “how we researched this” unless explicitly behind-the-scenes;
- vague “what happened today” hooks.

Example hook directions:
- “The Senate rejected Lula’s STF nominee.”
- “This was the first rejection in 132 years.”
- “The headline says one thing. The document says something narrower.”

## Shared storytelling layer to add to generation

The reviewer has a quality gate now, but the generator still needs to be updated.

When patching `carousel_builder.py`, add these fields or equivalent internal prompt requirements:

- `central_tension`
- `audience_question`
- `connective_thread`
- `proof_moment`
- `compression_note`
- `final_clarity`
- `research_branching_needed`
- `slide_reward_arc`
- `hook_options`
- `selected_hook`
- `hook_pattern`
- `why_this_hook`
- `hook_risk_check`

## Research branching requirement

Priscila specifically asked for branching research logic.

The pipeline should not force weak content just because the template needs a slide.

Required logic:
1. Start with the main topic/claim.
2. Identify the central point.
3. Identify 2–3 supporting points.
4. Check if each supporting point has enough evidence and context.
5. If a point is weak, unclear, or unsupported, branch into follow-up research.
6. After research:
   - keep it if supported;
   - soften it if partially supported;
   - remove it if unsupported.
7. Only then write the carousel.

## Slide reward arc requirement

Priscila wants slides to build curiosity and deliver the reward closer to the end when appropriate.

Required arc:
- Cover: strong hook / central tension.
- Early slides: why this matters and what the audience is missing.
- Middle slides: evidence, context, or breakdown.
- Later slides: reward/payoff — clearest answer, verdict, reveal, or practical takeaway.
- Final slide: sources/CTA/final clarity.

This should connect to carousel templates created in another chat. The template selector/registry should not only choose a visual template; it should understand whether the selected template can support the hook/story arc.

## Carousel-template connection

Priscila mentioned that carousel templates were created in another chat.

Next chat must connect this work with that template work.

Important:
- Do not force every topic into one storytelling format.
- Match topic + niche + goal to the right carousel template.
- Some templates may support multi-slide “tip of the day” groups and should stay together.
- If a template has a multi-slide set, do not mix one slide from that set with other unrelated templates.
- For OPC content, only choose from OPC-approved templates/sections.
- The storytelling layer should help choose or validate the template fit.

Recommended next logic:
1. Identify niche: OPC, Brazil News, USA News.
2. Identify content type: fact-check, chain/explainer, homeowner tip, progress, before/after, source receipt, etc.
3. Choose template family from the correct niche section.
4. Generate 3–5 hook options.
5. Select best hook based on niche, credibility, intrigue, and visual fit.
6. Confirm template supports the slide reward arc.
7. Generate content.
8. Run reviewer.
9. If reviewer flags hook/story_arc/proof_gap, rewrite or research more.

## What was actually implemented in code

File:
- `scripts/content_creator/text_reviewer.py`

Added:
- `HOOK_STORYTELLING_REVIEW_RULES`

It now instructs the reviewer to flag:
- average/generic hooks;
- misleading or clickbait hooks;
- hooks that do not create claim/tension/number/risk/consequence/contradiction/curiosity gap;
- News hooks that wrongly become “how we verified this”; 
- OPC hooks that sound salesy or promise outcomes;
- weak story arcs;
- missing payoff;
- weak or unsupported supporting points/proof gaps.

Reviewer issue types expanded to include:
- `hook`
- `story_arc`
- `proof_gap`

## What still needs to be implemented

### 1. Patch `carousel_builder.py`

Patch generation prompts/schema carefully.

Do not rewrite the whole file.
Do not remove existing templates.
Do not remove `OPC_COPY_RULES`.
Do not remove `BRAZIL_COPY_RULES`.
Do not weaken existing guardrails.

Add generation-side hook/storytelling fields:
- hook options;
- selected hook;
- hook pattern;
- hook risk check;
- central tension;
- audience question;
- connective thread;
- proof moment;
- compression note;
- final clarity;
- research branching flag;
- slide reward arc.

### 2. Patch News capture/content brief if needed

The Capture Pipeline creates analysis docs and bilingual content briefs.

If the brief feeds the carousel, it should include the same logic:
- strong hook options;
- claim/source/receipt framing;
- proof gaps;
- follow-up research needed;
- final clarity / payoff.

### 3. Run samples

Run at least:
- 1 OPC sample;
- 1 Brazil News or USA News sample.

Check:
- hooks are not average;
- News does not become process-focused;
- sources/receipts are visible;
- OPC is homeowner-safe;
- slide arc has payoff;
- reviewer can flag bad hooks.

## Verification checklist before marking done

Do not mark Issue #122 done until this is verified.

1. Confirm `OPC_COPY_RULES` still exists and was not weakened.
2. Confirm `BRAZIL_COPY_RULES` still exists and was not weakened.
3. Confirm existing template structures were not removed.
4. Confirm generation prompt includes hook/storytelling fields.
5. Confirm reviewer includes `HOOK_STORYTELLING_REVIEW_RULES`.
6. Confirm generic hooks are flagged.
7. Confirm News hooks remain claim/tension/source/result based.
8. Confirm News posts show receipts/sources, not behind-the-scenes verification, unless requested.
9. Confirm OPC hooks remain risk/cost/decision based and not salesy.
10. Confirm research branching or proof-gap behavior exists.
11. Confirm carousel builds cover → context → evidence → payoff → final clarity.
12. Save commit SHA, run ID, sample output links, and verification result in Issue #122.

## Current final status

Partial implementation complete.

The reviewer has been strengthened.

The generator still needs to be strengthened.

Next chat should continue by patching `carousel_builder.py` and connecting this to the carousel-template mapping/selection work from the other chat.
