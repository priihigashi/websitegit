"""
script_generator.py — Uses Claude to pick 2 Talking Head topics and write scripts.
Topics must be things Mike can speak to without being on a specific job site.
Scripts must be under 60 seconds (~130 words).
"""
import os, json
import anthropic

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM = """You are a content strategist for Oak Park Construction, a contractor company in Oak Park, IL.
Mike is the owner — he appears on camera for Talking Head videos.

GOOD topics for Talking Head: industry tips, common homeowner mistakes, what to ask a contractor,
pricing transparency, project timelines, seasonal advice, mindset/business insights.
BAD topics: anything requiring Mike to be at a specific active job site or use equipment only found on-site.

Write scripts as natural speech — how Mike would actually say it out loud. Conversational, not corporate."""


def pick_topics_and_write_scripts(scraped_results):
    """
    Takes list of scraped + filtered content results.
    Returns list of 2 script objects.
    """
    summary = [
        {
            "url":     r["url"],
            "views":   r["views"],
            "caption": r["caption"][:200],
            "niche":   r["niche"],
            "platform": r["platform"],
        }
        for r in scraped_results[:30]
    ]

    prompt = f"""Today's top-performing content (all 10k+ views):

{json.dumps(summary, indent=2)}

Pick the 2 best topics for Oak Park Construction Talking Head videos that Mike can film
WITHOUT being on a specific job site. For each topic write:
1. topic — 5 words max, punchy
2. why_trending — 1 sentence on why this is resonating right now
3. script — under 60 seconds / ~130 words, conversational first-person as Mike
4. inspo_url — URL from the list above that inspired this topic
5. estimated_seconds — your best guess at speaking time

Return ONLY a valid JSON array with exactly 2 objects:
[
  {{
    "topic": "...",
    "why_trending": "...",
    "script": "...",
    "inspo_url": "...",
    "estimated_seconds": 45
  }},
  {{ ... }}
]"""

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2000,
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
