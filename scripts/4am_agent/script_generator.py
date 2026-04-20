"""
script_generator.py — Uses Claude to pick 2 Talking Head topics and write scripts.
Topics must be things Mike can speak to without being on a specific job site.
Scripts must be under 60 seconds (~130 words).
"""
import os, json
import anthropic

client = anthropic.Anthropic(api_key=os.environ["CLAUDE_KEY_4_CONTENT"], timeout=120.0)

SYSTEM = """You are a content strategist for Oak Park Construction, a licensed general contractor
based in Pompano Beach, Florida, serving Broward County and South Florida.

The brothers:
- Matthew McFolling (Matt) — licensed contractor (CBC1263425), runs the jobs
- Michael McFolling (Mike) — project manager, appears on camera for all Talking Head videos

Brand story: The "Oak Park" name comes from their roots in Oak Park, Illinois. They brought
Midwest work ethic to South Florida — this is part of the brand and can be mentioned naturally.

Services: kitchen remodels, bathroom remodels, home additions, new construction (shell),
pergolas, outdoor kitchens, concrete work (driveways, patios), roofing, tile.

Market context: South Florida homeowners — hurricanes, high humidity, HOA rules,
open-concept living, indoor-outdoor spaces, snowbirds renovating before season.

GOOD topics for Talking Head: industry tips, common homeowner mistakes, what to ask a contractor
before hiring, pricing transparency, project timelines, South Florida seasonal advice
(hurricane prep, rainy season, HOA permit process), contractor red flags, material tips.

BAD topics: anything requiring Mike to be on a specific active job site or use equipment.

Voice: natural speech, how Mike would actually say it. Conversational, direct, confident.
South Florida references (humidity, weather, HOA, permits) where natural — not forced.
No corporate language. He speaks as a guy who grew up in the Midwest and now builds in Florida."""


def pick_topics_and_write_scripts(scraped_results):
    """
    Takes list of scraped + filtered content results.
    Returns list of 2 script objects.
    """
    summary = [
        {
            "url":      r["url"],
            "views":    r["views"],
            "caption":  r["caption"][:200],
            "niche":    r["niche"],
            "platform": r["platform"],
        }
        for r in scraped_results[:30]
    ]

    prompt = f"""Today's top-performing content in the construction/renovation space (all 10k+ views):

{json.dumps(summary, indent=2)}

Pick the 2 best topics for Oak Park Construction Talking Head videos that Mike can film
at home or in the office — NO job site required. Prioritize topics relevant to South Florida
homeowners (Broward County, Pompano Beach, Fort Lauderdale area).

For each topic write:
1. topic — 5 words max, punchy, specific
2. why_trending — 1 sentence on why this resonates right now in South Florida
3. hook — first 8 words Mike says — grabs attention immediately (no "hey guys", no fluff)
4. script — under 60 seconds / ~130 words, conversational first-person as Mike. Include hook as first line.
5. hashtags — 15 relevant hashtags: mix of local (#pompanobeach #southfloridahomes), niche (#kitchenremodel), and broad (#contractor). Space-separated with # prefix.
6. inspo_url — URL from the list above that inspired this topic
7. estimated_seconds — your best guess at speaking time

Return ONLY a valid JSON array with exactly 2 objects:
[
  {{
    "topic": "...",
    "why_trending": "...",
    "hook": "...",
    "script": "...",
    "hashtags": "#pompanobeach #southfloridahomes ...",
    "inspo_url": "...",
    "estimated_seconds": 45
  }},
  {{ ... }}
]"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2500,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    # Strip markdown code fences if present
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    return json.loads(text)
