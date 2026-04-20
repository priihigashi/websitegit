# FORMAT-001: Split-Screen News Reel — Video Spec v1.0

Approved format for Brazil/USA news fact-check reels.
Source of truth for all code in `scripts/remotion/`.

---

## Composition

| Property | Value |
|---|---|
| Width × Height | 1080 × 1920 px (9:16 vertical) |
| FPS | 30 |
| Default duration | 900 frames = 30 seconds |
| Color: obsidian | `#0E0D0B` — full background |
| Color: paper | `#F2ECE0` — primary text |
| Color: accent/gold | `#F4C430` — borders, highlights |
| Color: margin | `#6B6560` — secondary/source text |
| Font: headlines | Fraunces (serif), bold |
| Font: body | Inter (sans-serif) |
| Font: mono | JetBrains Mono (handles, sources) |

---

## Layout

```
┌─────────────────────────────────────┐  0px
│  [ topicTitle strip — visible after │  (hidden during hook)
│    frame 150 if hook is present ]   │
│                                     │
│  SPEAKER VIDEO (objectFit: cover)   │  ← top 58% = 1114px
│  objectPosition: 50% {videoOffsetY} │    default videoOffsetY = "15%"
│                                     │
│  [ speaker lower-third badge ]      │
│  [ SRT captions ]                   │
├─────────────────────────────────────┤  1114px (3px gold divider)
│                                     │
│  PROOF ZONE (obsidian bg)           │  ← bottom 42% = 806px
│  headline · fact · source           │
│  optional: grayscale image (left ½) │
│                                     │
│                    @HANDLE          │  bottom-right corner
└─────────────────────────────────────┘  1920px
```

---

## Phases & Timing

### Phase 1 — Hook Intro (frames 0–150 = 5 seconds)
**Purpose**: scroll-stopper. Large bold question/claim in the first 5 seconds.

- Full-width semi-opaque dark overlay ON TOP of the video zone
- Hook text: Fraunces serif, bold, ~110px, paper color, centered
- Text-shadow for readability over any video background
- Opacity animation: fade in 0→8 frames, hold 8→140, fade out 140→150
- During hook: `topicTitle` strip HIDDEN (hook occupies that visual space)
- During hook: source video plays at 60% opacity underneath (context visible)
- After frame 150: hook overlay disappears, video returns to 100% opacity
- If `hook` prop is empty or not provided → skip this phase entirely, Phase 2 starts at frame 0

### Phase 2 — Main Content (frames 150+ or 0+ if no hook)
- Source video at 100% opacity, `objectPosition: "50% {videoOffsetY}"`
- `topicTitle` strip appears at top of video zone (if provided)
- Speaker lower-third name badge (if `speakerName` provided)
- SRT captions burned in over video
- Gold divider at 1114px
- Proof slides animate in at their `startFrame`

---

## Props Reference

| Prop | Type | Required | Description |
|---|---|---|---|
| `videoSrc` | string | ✅ | Path to source clip (always `./public/source_clip.mp4`) |
| `videoStartFrame` | number | — | Frame in source clip to start from (skip intro) |
| `hook` | string | — | **Bold hook text** shown in first 5 seconds. Large, centered. |
| `proofSlides` | ProofSlide[] | — | Bottom-zone fact cards (timed). Empty = dark zone. |
| `captions` | CaptionEntry[] | — | SRT caption entries (burned in over video) |
| `language` | "en" \| "pt" | ✅ | Language (affects composition ID) |
| `totalFrames` | number | ✅ | Total duration in frames |
| `speakerName` | string | — | Lower-third name (e.g., "Marianne Williamson") |
| `speakerRole` | string | — | Lower-third role (e.g., "Author & Activist") |
| `topicTitle` | string | — | Small topic strip at top of video zone (e.g., "REGIME CHANGE") |
| `videoOffsetY` | string | — | Vertical crop anchor, default `"15%"`. Higher % = lower crop start |
| `voiceover_url` | string | — | Path to voiceover MP3 in public/ dir |

---

## Face Framing Rule

- **Source format**: always 9:16 vertical (Instagram/TikTok reels)
- **`objectFit: "cover"`** + **`objectPosition: "50% 15%"`** (default)
  - Horizontal: centered (50%)
  - Vertical: 15% from top — shows speaker's head/face which is typically in the upper 20-40% of a talking-head reel
- **`videoOffsetY` prop** allows per-render adjustment: 
  - `"0%"` = anchored to very top (old behavior — can cut top of head if strip covers it)
  - `"15%"` = default, good for most talking-head reels
  - `"25%"` = use when speaker is positioned lower in frame

---

## Proof Slide Rules

- At least 1 proof slide required to show content in bottom zone (empty = dark)
- Each slide: `headline` (bold claim) + `fact` (supporting detail) + `source` (attribution)
- Optional `imageUrl`: grayscale image fills left 50% of proof zone
- `startFrame` + `durationFrames` control when each slide appears
- Slides are exclusive — only one is active at a time

---

## Auto-Trigger Rule

**render-video.yml is ALWAYS manual** (`workflow_dispatch`).
The capture pipeline does NOT auto-trigger it. Capture completion email says:
> "Next step: trigger render-video.yml manually via Actions tab with story_id=..."

Never wire capture → render automatically without an explicit approved `render_video` flag.

---

## What Was Approved (git reference)

- `850f53c` — News FORMAT-001 initial build (split-screen, captions, proof zone) — originally tagged "SOVEREIGN"
- `523f1f1` — Renamed to NewsReel, added speaker/topic strip params

The `topicTitle` strip was always small (28px). The **hook intro sequence** is NEW (not in original). It was missing — this spec adds it for the first time.
