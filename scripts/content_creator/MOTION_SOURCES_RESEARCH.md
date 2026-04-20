# MOTION SOURCES — Research + Fallback Chain

Single source of truth for **where carousel/reel clips come from**, in order of priority, and **how Claude-the-pipeline should talk to each source**. Built 2026-04-20 from live tool documentation, not memory.

Philosophy (Priscila, 2026-04-20):
> "so many fallbacks that something will go through."

Motion is default-ON for every carousel (`MOTION IS DEFAULT ON` in CLAUDE.md). If the clip source chain returns nothing, we still ship — Ken Burns on the static PNG is the last-resort safety net.

---

## RENDERER CASCADE (how the motion MP4 gets made)

For EACH slide that should have motion:

1. **Remotion** (preferred) — React-source composition `CarouselMotion` (1080×1350, 5 sec loop). Used when a clip MP4 is available AND renderer is explicitly `remotion` (or cover-slide default).
2. **Playwright** — HTML-source record via `record_motion.js`. Used for mid-carousel slides with clip + newspaper/bio frame (existing Rachadinha pattern).
3. **Ken Burns (ffmpeg zoompan)** — last resort. Zooms the static PNG. ALWAYS works because it needs no clip.

Rule: `Remotion → Playwright → Ken Burns`. Ken Burns is the floor — nothing lower, nothing replaces it.

Why two deterministic renderers (Remotion + Playwright)?
- **Remotion** gives us React-quality typography + timeline-accurate animations (uses the same composition family as `NewsReel`). Best for covers + hook slides.
- **Playwright** keeps the HTML templates we already hand-author (Rachadinha, Walnut Kitchen, etc.) intact — no rewrite tax. Best for slides whose look is already locked in HTML.

The cascade per slide is chosen by `motion_renderer` in the Claude Haiku content JSON (see schema below).

---

## SOURCE CHAIN (where to find the clip)

Each entry below has: priority, what it returns, how we call it, auth, cost, rate limits, failure behavior.

### #1 — Apify YouTube Scraper + Downloader (existing)

- **Priority:** primary for people + institutions.
- **Why first:** YouTube has the biggest archive of speeches, press conferences, Senado/STF sessions, news crews. For "Trump oval office" or "Lula Planalto" you will almost always find a clip.
- **Auth:** `APIFY_API_KEY` (GitHub secret — confirmed set 2026-04-20).
- **Actors:**
  - Search: `streamers~youtube-scraper` — input `{"searchTerms": [query], "maxResults": 1, "saveVideos": false}`. Returns `url` / `videoUrl`.
  - Download: `streamers~youtube-video-downloader` — input `{"videoUrls": [{"url": URL}], "format": "mp4", "quality": "360p"}`. Returns `downloadUrl` / `url`.
- **Cost:** Apify usage credit per run. Cheap at 360p.
- **Rate limit:** sequential per actor (we already serialize).
- **Fail modes:** GitHub Actions runner IPs are sometimes YouTube-blocked — if the download URL 403s, fall through to next source.

### #2 — Apify Instagram Scraper (NEW 2026-04-20)

- **Priority:** second choice when YouTube misses or the subject lives on Instagram (creator reels, politician IG posts, breaking street video).
- **Why second:** IG has unique video that never reaches YouTube (Ramón Ortiz, Brazilian influencer reels, etc.).
- **Auth:** same `APIFY_API_KEY`.
- **Actor:** `apify~instagram-scraper`. Input shape (confirmed via Apify docs 2026-04-20):
  ```json
  {
    "search": "query string",
    "searchType": "hashtag",
    "searchLimit": 1,
    "resultsType": "reels",
    "resultsLimit": 3
  }
  ```
  Or direct URL lookup: `{"directUrls": ["https://www.instagram.com/reel/..."]}`.
- **Output:** each item has `videoUrl` (direct CDN link, short-lived). Download immediately.
- **Cost:** $1.50 per 1000 results (Apify list price 2026-04-20).
- **Rate limit:** default actor concurrency. We keep `resultsLimit` low (1–3).
- **Fail modes:** IG CDN URLs expire fast — always download in the same function call as the search. Never cache the URL.

### #3 — Pexels Videos API (existing)

- **Priority:** third — places, events, institutions, generic B-roll. NOT specific people.
- **Why third:** free, stable, no scraping, but library is stock — you will not find "Lula at Planalto 2024" here. You will find "Brasília government building".
- **Auth:** `PEXELS_API_KEY` (GitHub secret — confirmed set 2026-04-20).
- **Endpoint:** `GET https://api.pexels.com/videos/search?query=...&per_page=5&size=medium&orientation=portrait`
- **Header:** `Authorization: <key>` (NOT `Bearer <key>` — Pexels is quirky).
- **Output:** `videos[].video_files[]` each with `link`, `file_type`, `width`, `height`. Pick first MP4 with `height >= width` for portrait.
- **Cost:** free.
- **Rate limit:** 200 requests/hour default, 20000/month. Hit 429 → skip.
- **Fail modes:** zero hits for political/news queries. Use only when visual_hint is `context-image`, `place`, or `event`.

### #4 — Pixabay Videos API (NEW 2026-04-20)

- **Priority:** fourth — sibling stock source to Pexels. Often has different library so it catches what Pexels misses.
- **Auth:** `PIXABAY_API_KEY` (GitHub secret — **NOT yet set 2026-04-20**; code must tolerate missing key and skip silently).
- **Endpoint:** `GET https://pixabay.com/api/videos/?key={KEY}&q={query}&per_page=3&orientation=vertical`
- **Output:** `hits[].videos.{large,medium,small,tiny}` — each with `url`, `width`, `height`, `size`. Prefer `medium` for 360p-equivalent balance.
- **Cost:** free.
- **Rate limit:** **100 req / 60s** (confirmed via Pixabay docs 2026-04-20). HTTP 429 when exceeded. Must cache per-query for 24h per Pixabay TOS.
- **Fail modes:** no key → skip. 429 → skip this run, try next source.

### #5 — Archive.org (NEW 2026-04-20)

- **Priority:** fifth — public domain / CC news reels, historic speeches, old government footage. Strong for "history time" series and Cold War / sanctions contextual B-roll.
- **Auth:** none (public).
- **Search endpoint:** `GET https://archive.org/advancedsearch.php?q={query}+AND+mediatype%3Amovies&fl[]=identifier&fl[]=title&fl[]=downloads&rows=5&output=json`
- **Download URL format:** `https://archive.org/download/{IDENTIFIER}/{FILENAME}.mp4` (identifier from search, filename from metadata API).
- **Metadata endpoint:** `GET https://archive.org/metadata/{IDENTIFIER}` — returns `files[]` with filename + format. Pick `h.264 MP4` or `MPEG4`.
- **Cost:** free.
- **Rate limit:** polite use only — add `sleep(1)` between requests; no hard quota documented.
- **Fail modes:** some items are audio-only; filter by file format before downloading. Some files are multi-GB full films — cap download size at 50MB per clip.

### #6 — Wikimedia Commons (NEW 2026-04-20)

- **Priority:** sixth — CC-licensed video, often of historical events, protests, natural phenomena, government proceedings.
- **Auth:** none (public MediaWiki API).
- **Search endpoint:** `GET https://commons.wikimedia.org/w/api.php?action=query&list=search&srsearch={query}+filetype%3Avideo&srnamespace=6&format=json`
- **Download URL:** returned file path → `https://upload.wikimedia.org/wikipedia/commons/...`
- **Output:** WEBM or OGV (rarely MP4) — we ffmpeg-transcode to MP4 before using.
- **Cost:** free.
- **Rate limit:** 200 req/s per IP policy; not a concern.
- **Fail modes:** library is mostly image, video hits are sparse. Good for Cuba / Venezuela / Brasília archival.

### #7 — Mixkit / Videvo / Coverr (NEW — scraper fallback)

- **Priority:** seventh — free stock video sites without official APIs. Scraped via generic Apify actor or yt-dlp.
- **Auth:** none for Mixkit/Coverr direct downloads. Videvo needs a free account (future work).
- **Method:** yt-dlp on the page URL (Mixkit pages expose `og:video` meta) OR Apify `apify~web-scraper` actor with a page.js extractor.
- **Cost:** free.
- **Fail modes:** scraper can break on site redesigns; mark this tier optional + silent-fail.

### #8 — (skip) → Ken Burns

If slots #1–#7 all return nothing for a slide: no clip. `record_motion_slides` (Playwright) is skipped for that slide. `render_motion_cover` (Ken Burns ffmpeg) runs on the static PNG. Motion is still shipped. This is the floor.

---

## CONTENT JSON — motion fields per slide (Claude Haiku schema extension)

Claude Haiku (`generate_carousel_content`) already emits `clip_suggestions[]`. We extend each entry:

```json
{
  "slide": 2,
  "youtube_query": "Rodrigo Pacheco Senado sessão",
  "instagram_query": "Rodrigo Pacheco senado",
  "pexels_query": "brasilia senate building",
  "pixabay_query": "brazil senate aerial",
  "archive_query": "brazilian senate session",
  "motion_prompt": "Slow cinematic pan across senator speaking at podium, documentary style, 5s loop",
  "motion_renderer": "playwright",
  "visual_hint": "bio-card"
}
```

Field guide:
- `youtube_query` — existing, primary search term for Apify YouTube.
- `instagram_query` — optional, IG-specific phrasing (lowercase, likely to match hashtag).
- `pexels_query` — stock-style phrasing (no proper names).
- `pixabay_query` — stock-style phrasing (may differ from Pexels).
- `archive_query` — long-form phrasing, archival style ("1989 Berlin wall fall").
- `motion_prompt` — free text prompt for the Kling/Replicate animated cover AND as a reference for Remotion camera movement. NOT used by YouTube/stock searches.
- `motion_renderer` — enum `remotion` | `playwright` | `kenburns`. Default by slide position:
  - cover (slide 1) → `remotion`
  - middle clip slides → `playwright`
  - all others → `kenburns` (skip actual render, fall through to ffmpeg on PNG)
- `visual_hint` — existing (`bio-card`, `context-image`, `product-photo`, `icon-row`, `none`). Still drives whether Pexels is even tried for that slot.

Backward compatibility: if any new field is missing, `motion_sources.py` uses `youtube_query` as the universal fallback query and picks renderer by slide position.

---

## RESOURCES FOLDER LAYOUT

Per-topic `<workdir>/<post_id>/` work directory mirrors to Drive:
```
<post_id>/
  png/                               (static PNGs — variants × slides)
  motion/                            (MP4 + GIF + preview frame per slide)
  clips/                             ← NEW: raw downloaded source clips, attribution notes
    slide1_trump_oval_office.mp4
    slide1_trump_oval_office.source.txt      (url, source tier, license, fetched_at)
    slide3_brasilia_senate_pexels.mp4
    slide3_brasilia_senate_pexels.source.txt
  resources/
    clips/                           ← UPLOADED to Drive: same files as work/clips
    images/
    image_suggestions.md
    clip_hints.md                    ← NEW: per-slide source queries + renderer choice
    story.docx
```

The `.source.txt` sidecar is mandatory for attribution when the clip is licensed (CC, Pexels, Pixabay). One line per field: `source_url=...`, `license=...`, `attribution=...`.

---

## WHAT STAYS, WHAT CHANGES

**Stays (do not rewrite):**
- `_fetch_youtube_clip_apify()` in carousel_builder.py — works, keep.
- `_fetch_pexels_video()` in carousel_builder.py — works, keep.
- `record_motion.js` + `record_motion_slides()` + `render_motion_cover()` — all work, keep.
- Kling/Replicate animated cover — additive, already wired.

**Changes (additive only):**
- NEW file `motion_sources.py` — wraps existing fetchers + adds Instagram, Pixabay, Archive.org, Wikimedia, stock-scraper tiers. Exposes ONE function `fetch_clip_with_fallback(slide_cfg, work_dir, filename) → path|""`.
- `carousel_builder.fetch_clips()` — call into `motion_sources.fetch_clip_with_fallback` instead of the inline fallback chain.
- `main.process_one_topic()` — renderer cascade: try Remotion for cover, fall through to Playwright, then Ken Burns.
- NEW Remotion composition `CarouselMotion` (1080×1350, 150 frames @ 30fps) — accepts `{clipSrc, posterPng, hookText, durationFrames}` and renders a loopable motion layer. Registered in `Root.tsx`.
- `generate_carousel_content` prompt — add motion fields to `clip_suggestions[]` schema block.

---

## FAILURE BEHAVIOR

Every tier fails silently and logs a one-liner:
```
  motion_sources: Apify YouTube miss for 'Rodrigo Pacheco senado'
  motion_sources: Apify Instagram miss for 'rodrigo pacheco senado'
  motion_sources: Pexels miss for 'brasilia senate building'
  motion_sources: Pixabay (no key)
  motion_sources: Archive.org miss for 'brazilian senate session'
  motion_sources: Wikimedia miss for 'senado federal'
  motion_sources: Stock scrapers off
  motion_sources: all sources empty for slide 3 — Ken Burns fallback on PNG
```

No tier throws. No tier aborts the run. The Ken Burns floor guarantees ship-ready motion even when every clip source is down.

---

## TESTING MATRIX

Before declaring `motion_sources.py` done, verify with a Brazil News topic:
- [ ] slide 1 cover: Remotion render with motion_prompt → MP4 lands in `motion/`
- [ ] slide 3 middle: Playwright record with Instagram clip fallback when YouTube dry → MP4 lands in `motion/`
- [ ] slide 5 sources: no clip attempted, Ken Burns on PNG → MP4 lands in `motion/`
- [ ] `clips/` subfolder has the raw MP4s + `.source.txt` sidecars
- [ ] Drive upload includes `resources/clips/`
- [ ] email preview links to `motion/` subfolder and shows all slides
- [ ] Pre-ship audit (`MOTION IS DEFAULT ON` rule) passes

---

Research sources (accessed 2026-04-20):
- Apify Instagram Scraper docs + input schema
- Pixabay API Video endpoint docs + rate-limit headers
- Archive.org advancedsearch.php + metadata API docs
- Remotion `npx remotion render` CLI flags
- Existing repo code: `carousel_builder.py::fetch_clips`, `main.py::record_motion_slides`, `scripts/remotion/src/NewsReel.tsx`
