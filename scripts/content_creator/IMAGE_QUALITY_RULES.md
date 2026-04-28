# IMAGE QUALITY RULES — Carousel Content Pipeline

> Read by: carousel_reviewer.py · carousel_builder.py · any agent or script that fetches or generates carousel images.
> This is the single source of truth for what PASS and FAIL mean for a carousel image.

---

## PASS — What makes an image acceptable

An image passes if it meets ALL of the following:

1. **Real content** — The image actually shows what the slide is about. A kitchen remodel slide must show a kitchen. A politician slide must show that politician.
2. **Real or photorealistic source** — One of:
   - Real photo from Wikimedia Commons (CC-licensed)
   - Real stock photo from Pexels or Pixabay (royalty-free)
   - AI-generated image that is visually indistinguishable from a real photo (no hallucination markers — see FAIL list below)
3. **Correct orientation** — Portrait (vertical) for Instagram (1080×1350). Never landscape.
4. **Not blank** — Image has visual variance (PNG stddev > 8 in grayscale). A solid-color or near-blank image FAILS.
5. **Sufficient size** — File > 10KB. Smaller files are usually placeholder renders or failed downloads.
6. **Correct dimensions** — 1080×1350px for final PNGs. Source images may differ; export_slides.js handles resizing.

---

## FAIL — What makes an image unacceptable

Reject any image with one or more of these markers:

- **Cartoon / illustration style** — flat color areas, cell shading, anime, watercolor, clipart aesthetic
- **3D render / CGI** — perfect geometry, too-clean surfaces, plastic-skin textures, render-farm lighting
- **AI hallucination artifacts** — extra fingers, merged faces, garbled text in image, impossible geometry, floating objects
- **Generic stock mismatch** — image returned by the search but does not match the slide claim (e.g., query = "oak park kitchen remodel" but image shows a generic white kitchen in a luxury condo)
- **Wrong subject** — wrong person's face, wrong building, wrong location
- **Watermark / logo from another brand** — visible Getty Images / Shutterstock / iStock watermarks
- **Wrong orientation** — landscape image used in a portrait slot

---

## PER-SLOT RULES

### Cover slot (slide 1)

| Subject type | Allowed sources | Banned |
|---|---|---|
| Named person (politician, public figure, named subject) | Wikimedia CC real photo ONLY → initials fallback if not found | AI-generated face (Gemini / Seedream / DALL-E / SDXL) — never |
| Place (country, city, building) | Wikimedia CC → Pexels → Pixabay → Gemini → Seedream → DALL-E → SDXL | No restriction beyond FAIL list above |
| Event (law, vote, decision) | Document screenshot / news headline crop → Wikimedia CC → AI composition | No restriction |
| Concept (abstract policy, ideology) | Wikimedia CC → AI typographic composition | No restriction |

**Critical rule:** AI-generated faces on named-person cover slides are a HARD FAIL regardless of realism. The pipeline must detect this via `media_provenance.json` (cover `source_type == "ai"` when `cover_visual.subject_type == "person"`) and flag `fix_type=regenerate`.

### Body / context-image slots (slides 2–N, visual_hint == "context-image")

| Priority | Source | Notes |
|---|---|---|
| 1st | Wikimedia Commons CC photo | Best for institutions, politicians, historical events |
| 2nd | Pexels royalty-free photo | Best for places, materials, construction scenes |
| 3rd | Pixabay royalty-free photo | Backup when Pexels returns nothing |
| 4th | Gemini Imagen | AI fallback — flag as `fix_type=regenerate` in review |
| 5th | Seedream 4 | AI fallback — flag as `fix_type=regenerate` in review |
| 6th | DALL-E 3 | AI fallback — flag as `fix_type=regenerate` in review |
| 7th | Replicate SDXL | Last resort AI — flag as `fix_type=regenerate` in review |

When an AI source is used for a body slide, the reviewer flags it because it means all real-photo tiers missed — the query was likely too generic or incorrect. The fix is always to improve the query and re-fetch, not to accept the AI image.

### Bio-card slots (visual_hint == "bio-card", mentioned_people)

- Source: Wikipedia REST API thumbnail → Wikimedia Commons search → `.bio-initials` fallback
- NEVER use stock (Pexels/Pixabay) or AI for face cards — people must be identifiable
- `.bio-initials` (2-letter initials card) is acceptable ONLY when no licensed photo exists anywhere
- Bio-card images must be portrait-cropped (face visible at 110×130px minimum)

---

## CONTEXT_IMAGE_QUERY RULES — What makes a query good vs. bad

The `context_image_query` field drives ALL three real-photo tiers (Wikimedia, Pexels, Pixabay).
A bad query guarantees AI fallback. A good query gets a real photo on the first try.

### PASS — Good query patterns

- Must include: **subject** + **action or material** + **location context**
- Examples (OPC):
  - `oak park illinois exterior kitchen addition contractor`
  - `concrete driveway residential pour south florida`
  - `bathroom tile remodel frameless shower door installation`
  - `shiplap wood accent wall interior residential`
  - `GAF roof shingles installation aerial residential`
- Examples (Brazil/USA News):
  - `Câmara dos Deputados Brasília fachada aérea`
  - `Viktor Orbán 2024 election campaign Hungary`
  - `Supremo Tribunal Federal Brasília entrada`
  - `Jeffrey Epstein court documents federal trial`

### FAIL — Banned generic query patterns

These queries return unusable generic images. The builder prompt must never emit them:

- `construction work` — too broad, matches millions of unrelated images
- `house` — returns everything; useful for nothing
- `renovation` — same problem as "house"
- `contractor` — too broad
- `kitchen` — missing material + action + location context
- `bathroom` — same
- `home improvement` — useless on any stock API
- `building` — same as "house"
- `outdoor` / `indoor` — meaningless alone
- Any 1–2 word query without location or material specificity

**Rule:** A `context_image_query` must be ≥ 4 words. It must contain at least one location/context signal (Florida, Oak Park, Brasília, Hungary) OR at least one material/action signal (shiplap, concrete pour, tile installation). Anything shorter or more generic is a FAIL and must be regenerated.

---

## PROVENANCE — How the reviewer detects quality issues

Every build writes `resources/media_provenance.json` with this structure:

```json
{
  "cover": {
    "provider": "wikimedia|pexels|pixabay|gemini|seedream|dall-e-3|sdxl",
    "source_type": "cc|stock|ai",
    "query": "the search query used",
    "prompt": "the AI prompt if source_type=ai"
  },
  "slides": {
    "2": { "provider": "...", "source_type": "...", "query": "...", "prompt": "..." },
    "3": { "provider": "...", "source_type": "...", "query": "...", "prompt": "..." }
  }
}
```

**Reviewer logic:**
- `source_type == "ai"` on any slide → all real-photo tiers missed → `fix_type=regenerate`
- `source_type == "ai"` on cover AND `subject_type == "person"` → CRITICAL flag (editorial rule violation)
- `source_type == "cc"` or `source_type == "stock"` → real photo used → pass this check

The fix for every `regenerate` flag is always: improve the `context_image_query` to be more specific and re-run the media fetch step. Never accept AI body images as "good enough."
