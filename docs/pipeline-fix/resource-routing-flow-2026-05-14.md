# Resource Routing Flow — Notes, Transcript, Clips, Images

Status: DRAFT IMPLEMENTATION PLAN — first parser patch started 2026-05-14.

## Answer

We do not have the full flow end-to-end yet.

We do have enough pieces to wire it fast:
- `capture_pipeline.yml` and `scripts/capture/capture_pipeline.py` already download/transcribe the seed URL.
- `--url2` already handles one companion clip.
- `scripts/capture/person_evidence_dispatcher.py` already auto-dispatches `video-research.yml` when notes ask for more same-person clips.
- `video-research.yml` and `scripts/youtube_research.py` already research videos and write high-relevance URLs to Clip Collections.
- `scripts/content_creator/main.py` already gates `clips_needed=true` rows, triggers `video-research.yml`, and uploads `resources/clips/` into the final version folder.
- `scripts/content_creator/motion_sources.py` already fetches clips for motion and supporting B-roll.
- `scripts/content_creator/note_parser.py` now emits resource-routing labels for note URLs and research-needed requests.

The missing layer is one resource router that reads notes/transcripts, creates jobs, downloads or dispatches research, then writes a manifest the builder/editor can obey.

## The Two Required Flows

### Flow A — Links Already In Notes

Input:
- Priscila gives one or more URLs in notes.
- Notes may include role words: hook, repost, use on slide 3, cut at 0:18, main point, show this object.

Target behavior:
1. Parse notes.
2. Detect URLs.
3. Create one resource job per URL.
4. Download video/image resources.
5. Save video resources to `resources/clips/`.
6. Save image resources to `resources/images/`.
7. Write `resources/resource_manifest.json`.
8. Builder/editor reads manifest and knows:
   - which source URL produced each file,
   - which slide it belongs to,
   - what timestamp or cut hint to use,
   - whether the item is required or optional.

Current status:
- Parser support: STARTED.
- Downloader wiring: missing.
- Manifest writing: missing.
- Builder/editor consumption: missing.

### Flow B — No Links, Research The Topic

Input:
- Notes say: need more videos for this, find public clips, research videos, bring videos, look up this person/object/topic.
- Transcript may also reveal missing resources: named person, important object, key scene, proof gap, image gap.

Target behavior:
1. Parse notes and transcript.
2. Label missing resources:
   - `video_research_needed`
   - `person_research_needed`
   - `image_research_needed`
   - `proof_required`
   - `clip_discovery_needed`
3. Dispatch the correct route:
   - person evidence → `video-research.yml` mode `person_evidence_mining`
   - topic clips → `video-research.yml` mode `general`
   - image/object proof → image research route, then `resources/images/`
4. Hold the build when research is required before posting.
5. When enough clips exist, builder resumes and archives resources into the version folder.

Current status:
- Person evidence route: partially wired and already dispatches.
- General clip route: exists through clip gate, but not directly from parsed notes.
- Image/object route: not wired.
- Transcript-driven resource labeling: not wired.

## Labels And Required Functions

`note_parser()` should be treated as the first routing gate.

New parser fields added:
- `note_urls`: explicit URLs found in notes.
- `resource_requests`: normalized resource jobs.

Examples:

```json
{
  "type": "download_note_link",
  "kind": "video_clip",
  "source_url": "https://www.instagram.com/reel/ABC123/",
  "target": "resources/clips",
  "role": "note_link",
  "slide_hint": "slide 1",
  "cut_hint": "hook",
  "priority": 1
}
```

```json
{
  "type": "research_videos",
  "kind": "video_clip",
  "query": "Go research on this topic and bring videos about this senator...",
  "target": "Clip Collections",
  "downstream_target": "resources/clips",
  "niche": "brazil",
  "hold_build": true
}
```

## Manifest Shape

Write this file wherever resources are staged:

`resources/resource_manifest.json`

Minimum shape:

```json
{
  "story_id": "BCI-202605141230",
  "topic": "short topic",
  "niche": "brazil",
  "created_at": "2026-05-14T12:30:00-04:00",
  "source": "notes|transcript|research",
  "items": [
    {
      "id": "clip_001",
      "kind": "video_clip",
      "source_url": "https://...",
      "local_path": "resources/clips/clip_001.mp4",
      "drive_file_id": "",
      "slide_hint": "slide 1",
      "cut_hint": "0:18-0:26",
      "usage": "hook",
      "required": true,
      "status": "downloaded"
    }
  ]
}
```

## Fastest Implementation Order

1. Parser labels — started.
   - Add `note_urls`.
   - Add `resource_requests`.
   - Tests cover Flow A and Flow B.

2. Capture-side router.
   - New module: `scripts/capture/resource_router.py`.
   - Input: `story_id`, `project`, `notes`, `transcript`, `seed_url`.
   - Reuse `note_parser()` in rule mode first, LLM later.
   - For `download_note_link`, call the existing `download_video()` / `download_audio()` path where possible.
   - For research jobs, call existing dispatchers instead of creating new search code.

3. Manifest writer.
   - Write local `transcripts/<story_id>_resource_manifest.json`.
   - Upload manifest to the same capture Drive folder with `supportsAllDrives=True`.

4. Builder bridge.
   - When content_creator builds from a capture brief, fetch/read resource manifest if available.
   - Copy manifest-referenced clips/images into the working folder before `fetch_clips()`.
   - Upload to `resources/clips/` and `resources/images/` in the version folder.

5. Transcript resource labeling.
   - After transcription, run the same router on transcript excerpts.
   - Start conservative: only labels, no download, unless the note explicitly asks for it.

## What We Should Not Do

- Do not create a parallel downloader workflow.
- Do not bypass `video-research.yml` for research cases.
- Do not bypass `resources/clips/` and `resources/images/`.
- Do not upload bytes through Drive MCP `create_file`.
- Do not make the builder guess which clip belongs to a slide without a manifest.
- Do not let research-required notes build before the research job is done.

## Files Touched By This Surface

Workflows:
- `.github/workflows/capture_pipeline.yml`
- `.github/workflows/video-research.yml`
- `.github/workflows/content_creator.yml`
- `.github/workflows/capture_queue.yml`
- `.github/workflows/scheduled_capture_poll.yml`

Scripts:
- `scripts/capture/capture_pipeline.py`
- `scripts/capture/person_evidence_dispatcher.py`
- `scripts/content_creator/note_parser.py`
- `scripts/content_creator/main.py`
- `scripts/content_creator/motion_sources.py`
- `scripts/content_creator/topic_picker.py`
- `scripts/youtube_research.py`
- `scripts/research/person_evidence_runner.py`
- `scripts/4am_agent/scraper.py`
- `scripts/4am_agent/sheets_writer.py`

Docs / registries:
- `PIPELINE_REGISTRY.md`
- `PIPELINE_FIX_SEQUENCE.md`
- `docs/pipeline-fix/*`

## Implementation Started

Changed:
- `scripts/content_creator/note_parser.py`
- `scripts/capture/resource_router.py`
- `tests/test_note_parser.py`
- `tests/test_resource_router.py`

Verified:
- Direct Python execution of note parser + resource router tests passed.
- `pytest` was not installed in the local Python, so tests were invoked directly.

## Implementation Completed — 2026-05-14

Built end-to-end on top of the scaffolding above. Both Flow A and Flow B now
download, upload, write a clips manifest, and (for Flow B) email Priscila for
approval. Drive upload is deterministic OAuth + `MediaFileUpload` with
`supportsAllDrives=True` — no banned MCP `create_file` content uploads.

Added:
- `scripts/capture/video_downloader.py` — resource downloader with two entry points:
  * `download_url(url, …)`  → Flow A direct download. Instagram uses the same
    Apify/CDN route as the existing capture pipeline first; `yt-dlp` and cookies
    are fallback only. YouTube tries `yt-dlp`, then the existing Apify YouTube
    actor route when `yt-dlp` is blocked.
  * `search_youtube(query, n_results=3, search_size=5, …)` → Flow B
    `ytsearch5:<query>` + download top 3 candidates.
  * Duration extracted via `ffprobe`, falls back to `yt-dlp --dump-single-json`.
  * Staging dir defaults to `/tmp/clips/<story_id>/`.
- `scripts/capture/clips_manifest.py` — reader/writer for
  `resources/clips/clips.json`. Supports `make_entry`, `load`, `save`, `upsert`,
  `upsert_many`. Entry shape includes `source_url`, `local_path`,
  `duration_sec`, `suggested_cut_start`, `target_slide`, `status` (STAGED |
  CANDIDATE | APPROVED | REJECTED | DOWNLOAD_FAILED), plus
  `drive_file_id` / `drive_view_link` / `flow` / `search_query`.

Extended:
- `scripts/capture/resource_router.py` — added:
  * `execute_resource_jobs(manifest, …)` — runs every job in the manifest:
    Flow A → `download_url` → upload to per-story subfolder under the niche
    capture folder (via `routing.capture_folder`) → upsert clips.json with
    `status=STAGED`.
    Flow B → `search_youtube` → upload each candidate → upsert clips.json
    with `status=CANDIDATE` → fire `send_email.yml` via `gh workflow run` with
    formatted approval body listing title, duration, source URL, Drive link.
  * `route_and_execute(...)` — one-call helper: parse → write manifest →
    execute jobs → re-write manifest with execution metadata.
  * `upload_clip_to_drive(...)` — OAuth Python + `MediaFileUpload` +
    `supportsAllDrives=True`. Per-story subfolder created via
    `_ensure_subfolder`.
- `scripts/capture/capture_pipeline.py` — added a non-blocking hook right
  after `person_evidence_dispatcher` that calls `route_and_execute(...)` for
  every capture run. Failures are logged but never break the capture above.

New workflow:
- `.github/workflows/resource_downloader.yml` — manual `workflow_dispatch`
  entry point for standalone runs (without a full capture). Inputs:
  `story_id`, `project`, `notes`, optional `seed_url`, optional
  `send_emails` toggle. Uses `SHEETS_TOKEN`, `APIFY_API_KEY`,
  `PRI_OP_YT_COOKIES`, `PRI_OP_IG_COOKIES`, `PRI_OP_GMAIL_APP_PASSWORD`.

Tests (all passing):
- `tests/test_clips_manifest.py` — 8 tests covering shape, roundtrip,
  upsert insert/update, bulk upsert, missing-file, malformed JSON.
- `tests/test_video_downloader.py` — 11 tests covering URL classification,
  cookie selection, Apify-first Instagram route, staging dir sanitization,
  success path, failure path, search parsing (network/subprocess calls mocked).
- `tests/test_resource_router.py` — extended with 3 executor tests:
  * Flow A end-to-end with mocked downloader + Drive — verifies STAGED clip
    written, drive_file_id propagated.
  * Flow B end-to-end with 3 fake candidates — verifies CANDIDATE entries +
    approval email triggered with 3 candidates in body.
  * No-jobs notes → safe no-op.
- `tests/test_note_parser.py` — 12 tests, including per-URL slide/role parsing
  so two links in one note do not both inherit the same slide hint.

Drive routing (verified against `scripts/routing.py`):
- Brazil → `1DZWbS4bF4XF_OjJSnD02WD2N83ljXwHd` (News/Brazil/Captures)
- USA → `1ZzrEmj3Smt0chr8CxiCOyroFCRzE-zU1` (News/USA/Captures)
- OPC → `19SIHYkGYM3EsaudQUGtnYLmhVTYfMkZh` (Marketing/Content/Captures)
- Each story gets its own `resources_<story_id>` subfolder so multiple
  captures do not collide.

How to run:
```
gh workflow run resource_downloader.yml \
  --repo priihigashi/oak-park-ai-hub \
  -f story_id=NWS-001 \
  -f project=brazil \
  -f notes='Use https://www.instagram.com/reel/ABC123/ as hook on slide 1.'
```

Or, automatic — any normal Capture Pipeline run will trigger it from the
notes field with zero extra config.

## Tracker — Current Goal State

Updated: 2026-05-14.

Goal:
- A normal capture should turn Priscila's notes into usable editing resources.
- Flow A: if notes already contain URLs, download those clips/images, save them,
  and expose them to the carousel/editor with slide/cut hints.
- Flow B: if notes ask for more videos, research candidates, download them,
  save them, ask for approval when needed, and expose approved/staged clips to
  the carousel/editor.
- Final success means the generated carousel version folder uses the staged
  resources instead of ignoring them or re-downloading unrelated clips.

Done:
- Notes parser emits resource jobs.
- Direct URL downloader exists.
- YouTube research downloader exists.
- `clips.json` writer exists.
- Resource router executes Flow A and Flow B.
- Capture pipeline auto-calls the resource router.
- Manual `resource_downloader.yml` exists.
- Tests cover parser, downloader, manifest, and router execution.
- Builder bridge is wired in `scripts/content_creator/main.py`: after
  `fetch_clips()`, it loads `<work>/resources/clips/clips.json`, injects
  `STAGED` / `APPROVED` entries with numeric `target_slide` and existing local
  files into the `clips` dict, and lets uncovered slides keep the existing
  `fetch_clips()` result.

Remaining Gap:
- Needs a live proof with a real URL in capture notes.
- The bridge currently uses `target_slide`; `suggested_cut_start` is preserved
  in `clips.json` but not consumed by the renderer yet.

## Reliability Patch — 2026-05-15

Audit correction after reviewing the existing capture pipeline:
- The downloader must not depend on Priscila manually refreshing Instagram or
  YouTube cookies. The existing capture system already avoids that by using
  Apify/official routes first and only using cookies/`yt-dlp` as fallback.

Patched:
- `resource_downloader.yml` now passes `APIFY_API_KEY`, has
  `permissions: actions: write, contents: read`, and fixes the `send_emails`
  boolean/string comparison.
- `video_downloader.py` now uses Apify Instagram video URL/CDN before `yt-dlp`.
  It also supports raw or base64 cookie secrets only as fallback compatibility.
  Instagram `/p/` posts with no video are staged as image resources instead of
  failing the whole run.
- `note_parser.py` now extracts numeric `target_slide` and role hints per URL
  (`hook`, `apology_video`, `main_point`, `proof_clip`) instead of one vague
  string hint shared across nearby links.
- `resource_router.py` writes `story_id`, numeric `target_slide`, logs failed
  jobs to the Pipeline Failures sheet, and exits non-zero when executable jobs
  fail.
- `capture_pipeline.py` stages resource clips under `transcripts/resources/clips`
  during normal capture runs so the capture artifact includes `clips.json`.
- `main.py` bridge now downloads missing local clip files from Drive using
  `drive_file_id` before injecting clips into render.

Verified locally:
- `python3.12 -m pytest tests/test_note_parser.py tests/test_resource_router.py tests/test_clips_manifest.py tests/test_video_downloader.py -q`
  → 38 passed.
- Python compile check passed for the edited scripts.
- `resource_downloader.yml` parsed successfully with Ruby YAML.

Live proof:
- Run `25927085076` passed on GitHub Actions.
- Drive folder: `resources_NWS-SOPHIA-001`
  (`1360MvheZEOTsrk1tg7lePJN3ymOVAZGT`).
- Uploaded:
  * `clip_001_apify.jpg` — Instagram `/p/` post staged as image resource,
    status `STAGED`, `media_kind=image`, `target_slide=1`,
    Drive file `1VMwYX3Cjk9cLBxW4h2uFJhNi0QaIrsv6`.
  * `clip_002_apify.mp4` — Instagram Reel staged as video resource,
    status `STAGED`, `media_kind=video`, `target_slide=2`, duration `43.84s`,
    Drive file `1NDg1IJcwM2BkYxJiG-ODgr19HI1kJgZD`.
  * `clips.json` — Drive file `1jSr-HGOidmnwxHPH_DEIYWnwoMDfNqP2`.

Interpretation:
- Flow A resource downloading is live for this proof case.
- The builder bridge will inject only `media_kind=video` clips into motion
  render; image resources are staged for the resources folder but not forced
  into the video-clip bridge yet.
- Need to confirm the final Drive version folder includes both the clip files
  and `clips.json`.

## Cross-Runner Bridge Patch — 2026-05-15

Audit correction before another live run:
- Added `story_id` columns to the live Ideas & Inbox spreadsheet:
  * `📋 Content Queue!AK1`
  * `📥 Inspiration Library!AH1`
- `capture_pipeline.py` now writes the capture `story_id` into Inspiration
  Library rows, including normal OPC/news/bias/unrouted paths.
- `topic_picker.py` now propagates `story_id` from Inspiration Library into
  Content Queue when the column exists.
- `main.py` now reads `story_id`/`capture_story_id` aliases from Content Queue
  and also accepts `CAPTURE_STORY_ID` from workflow dispatch.
- `content_creator.yml` now has a `capture_story_id` manual input, passes it as
  `CAPTURE_STORY_ID`, pins checkout/setup-python/upload-artifact, and sets
  `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true`.
- `main.py` fetches `resources_<story_id>/clips.json` and
  `story_resources.json` from Drive before content generation so the clip
  intelligence summary can influence the carousel prompt.

Verified locally:
- `python3.12 -m py_compile scripts/capture/capture_pipeline.py scripts/content_creator/main.py scripts/content_creator/topic_picker.py`
- `python3.12 -m pytest tests/test_note_parser.py tests/test_resource_router.py tests/test_clips_manifest.py tests/test_video_downloader.py -q`
  → 38 passed.
- `content_creator.yml` parsed successfully with Ruby YAML.

Current remaining proof:
- Run one normal capture with links in notes and confirm the generated
  Inspiration Library row has `story_id`.
- Promote/build the matching Content Queue row and confirm logs show:
  `[drive_fetch] fetched ...` and `clips.json bridge: merged ...`.
- Then run Flow B approval proof:
  research request → candidates → email → approval reply → Drive `clips.json`
  status flips to `APPROVED` → content creator uses approved clips.

Acceptance Criteria:
- Test with a real capture note containing a URL.
- Verify `clips.json` is created under the story resource folder.
- Verify the carousel build reads that manifest and logs
  `clips.json bridge: injected ...`.
- Verify `resources/clips/` in the final carousel version folder contains the
  staged clip and `clips.json`.
- Verify motion HTML/render uses the staged clip on the intended slide.
- Verify existing `fetch_clips()` still runs for slides not covered by the
  manifest.
