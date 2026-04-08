# Task: Build & Upload Carousel Design Templates to Drive

**Status:** Pending  
**Do on:** Desktop (Claude Code desktop app — needs Drive access + design work)

---

## Context

The carousel builder (`scripts/build_carousel_cloud.py`) already has the code infrastructure to:
- Load templates from Google Drive at runtime
- Match templates to content type (kitchen, bath, outdoor, before/after, carousel, reel)
- Composite the template as an overlay on every slide (PNG with alpha = true overlay)

**What's missing:** The actual template image files in the Drive folder. Nothing is there yet.

---

## Brand Reference Links

- **Brand doc (design specs):** https://docs.google.com/document/d/1bOel-Fae4jTes9t2vjvMCT1NcuDqr4PKMGg_Pi8Jnl8/edit?usp=drivesdk
- **Templates Drive folder ID:** `1564kppA5kuHgYXzj7ujjhSgyW3fc-jXR`
  - Full path: `ClaudeWorkspace / Content - Reels & TikTok / templates /`

---

## Brand Kit (already updated in script — use these for templates)

### Colors (exact Canva palette)
| Name       | Hex       | RGB            |
|------------|-----------|----------------|
| Yellow     | `#e0e84d` | (224, 232, 77) |
| Warm Brown | `#5b3c1f` | (91, 60, 31)   |
| Cream      | `#f0ede7` | (240, 237, 231)|
| Dark BG    | `#040606` | (4, 6, 6)      |
| Black      | `#000000` | (0, 0, 0)      |
| White      | `#ffffff` | (255, 255, 255)|

### Fonts
| Slot       | Font              | Use in slides              |
|------------|-------------------|----------------------------|
| Title      | Anton             | Big hook text (cover)      |
| Subtitle   | Gochi Hand        | Service label, sublabel    |
| Heading    | Roboto            | Section headings           |
| Subheading | Roboto Bold       | Slide labels               |
| Body       | Roboto            | Body text, captions        |
| Quote      | Gochi Hand        | CTA body text              |
| Caption    | Roboto            | Brand tag, small text      |

---

## What To Do

### Step 1 — Read the brand doc
Open the brand doc link above. Look for:
- Layout rules (where text goes, margins, spacing)
- Logo placement rules
- Any slide structure guidelines
- Color usage rules (when to use yellow vs cream vs dark bg)

### Step 2 — Create template PNG files in Canva (1080 × 1350px)
Create one template per content type as a **PNG with transparent background**.
Templates should define the frame/overlay only — borders, color bars, logo placement, text area guides — NOT the photo (that gets placed underneath by the script).

**Templates to create (filename = keyword the script matches on):**

| Filename                  | Content type          | Style notes                          |
|---------------------------|-----------------------|--------------------------------------|
| `carousel.png`            | Default carousel      | Yellow accent bar bottom-left, brand tag area |
| `kitchen.png`             | Kitchen remodel       | Warm brown accent, cream text areas  |
| `bath.png`                | Bathroom remodel      | Clean minimal, white/cream overlay   |
| `outdoor.png`             | Pergola / patio       | Dark BG overlay, yellow accents      |
| `before_after.png`        | Before & after posts  | Split indicator or divider element   |
| `reel.png`                | Reels                 | Vertical motion feel, bold accents   |

### Step 3 — Upload templates to Drive
Upload all PNGs to:
`ClaudeWorkspace / Content - Reels & TikTok / templates /`
(Drive folder ID: `1564kppA5kuHgYXzj7ujjhSgyW3fc-jXR`)

### Step 4 — Test run
Manually trigger the carousel workflow from GitHub Actions:
- Go to repo → Actions → "Build Carousels — Approved Posts" → Run workflow
- Check output in Drive → `Content - Reels & TikTok / Ready to Post /`
- Verify template overlays look correct on the slides

### Step 5 — Adjust if needed
If overlay looks off (too heavy, wrong placement), adjust template PNG opacity or layout in Canva and re-upload.

---

## How the Template System Works (for reference)

```
Script at runtime:
1. Connects to Drive templates folder
2. Lists all files → matches by filename keyword to content type
3. Downloads matching template PNG
4. Composites it OVER each slide after the photo + text are rendered
   - PNG with alpha channel = true transparent overlay ✓ (recommended)
   - JPEG = 12% blend (subtle texture)
5. Saves final slide and uploads to Ready to Post folder
```

---

## Files Changed (already done — in branch `claude/check-carousel-schedule-nhFKv`)

- `scripts/build_carousel_cloud.py` — brand colors, Gochi Hand font, template system
- `scripts/content_queue.py` — own feed diversity check via Apify

---

## Notes
- Templates are optional — if Drive folder is empty the script runs with default design (still looks good)
- Add `APIFY_API_KEY` to `ClaudeWorkspace/.env` to enable the feed diversity check in `content_queue.py`
