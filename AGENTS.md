# AGENTS.md — Priscila's Root Instructions for Codex & Other AI CLIs

> Source of truth: `priihigashi/oak-park-ai-hub` (GitHub). Local mirror at `~/AGENTS.md` + `~/.claude/CLAUDE.md`.
> For Claude Code: full rules live in `~/.claude/CLAUDE.md` (authoritative, longer).
> For shared reusable skills: `~/.agents/skills/<name>/SKILL.md`.

## Identity

User: Priscila Higashi. ADHD. Runs two businesses: Oak Park Construction (CBC1263425, priscila@oakpark-construction.com) + McFolling Properties (property management, client Matthew/Michael McFolling). Also manages mom's Brazil real estate site (hig-negocios-imobiliarios).

Style: direct, no preamble. Execute first, confirm after. One clear next action at a time. Short on mobile. Done = calendar event renamed `DONE ...`.

## Source of truth

- GitHub repo: `priihigashi/oak-park-ai-hub` (all automation, all secrets, all workflows)
- Local ClaudeWorkspace: `~/ClaudeWorkspace/`
- Credentials: `~/ClaudeWorkspace/Credentials/`
- Memory index: `~/.claude/projects/-Users-priscilahigashi/memory/MEMORY.md`
- Active connections & routing: `~/.claude/projects/-Users-priscilahigashi/memory/reference_active_connections.md`

Never use TXT as the primary source. Never rewrite working scripts from scratch.

## Ecosystem compatibility guardrail — READ BEFORE ACTING

Priscila's automation is a live ecosystem: GitHub Actions workflows, shared Drives, Composio MCP, OAuth tokens (`SHEETS_TOKEN`, `MCFOLLING_TOKEN`), the cron-driven 4AM agent, Canva MCP, GitHub secrets with strict naming. Before making any change:

1. Identify which part of the ecosystem the change touches (workflow, skill, secret, Drive folder, spreadsheet, Doc, MCP tool).
2. Verify the change is compatible with the existing routes in the connection-routing table — do not introduce a parallel route that bypasses existing automation.
3. If a change requires a new secret, new OAuth scope, or new MCP connection, stop and flag it — do not half-wire it.
4. Never "simplify" by stripping `supportsAllDrives=true`, the `PRI_OP_` secret prefix, or the Cloud-first/Composio-fallback routing. Those aren't style choices; they're required for things to keep working.
5. If you propose a refactor, list every workflow / script / skill that touches the surface area first. If you can't enumerate them, you haven't read enough.

## GitHub publishing guardrail — prevents sandbox-only fake work

If you are an AI running in a sandboxed environment (Codex, web Claude, etc.), your git operations may never reach the real GitHub repo. Before claiming any commit / PR / push is live:

1. Run `git remote -v`. The output MUST contain `github.com/priihigashi/oak-park-ai-hub` (or the relevant real remote). If it shows a local-only path or an unknown host, STOP — your changes are sandbox-only.
2. Run `git push` (or `git push -u origin <branch>` for a new branch) and confirm the push is accepted by the real remote. "Committed" ≠ "pushed".
3. Open PRs with `~/bin/gh pr create` (GitHub CLI) or the real `gh` binary on PATH. **`make_pr` is not a real command** — if you invoke it, you are hallucinating.
4. After claiming success, verify by reading the commit back from the remote: `gh api repos/priihigashi/oak-park-ai-hub/commits/<branch>` or `gh pr list`. If you can't see it on the remote, it didn't happen.
5. Never report "✅ committed" / "✅ PR opened" without completing step 4.

Sandbox paths like `/workspace/oak-park-ai-hub` are clones that Codex creates for its own execution. They are NOT the user's live repo. The user's live checkout is at `~/ClaudeWorkspace/oak-park-ai-hub` on her machine.

## Connection routing — Cloud first, Composio fallback

| Service | Primary | Fallback |
|---|---|---|
| Sheets | Google Sheets API via OAuth (SHEETS_TOKEN) | Composio `GOOGLESHEETS_*` |
| Docs (write) | Composio `GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN` | Python Docs API `docs.documents().batchUpdate` |
| Drive (search/list/read) | MCP `mcp__claude_ai_Google_Drive__*` → `mcp__gdrive__search` (skip on `-32603`) | OAuth Python `supportsAllDrives=true&includeItemsFromAllDrives=true` |
| Drive (upload bytes) | OAuth Python `googleapiclient` `MediaFileUpload` + `supportsAllDrives=True` | Raw REST `uploadType=multipart\|resumable&supportsAllDrives=true` |
| Calendar | MCP `mcp__claude_ai_Google_Calendar__*` (deferred — load schema first) | Composio `GOOGLECALENDAR_CREATE_EVENT` → Python OAuth |
| Gmail (draft) | MCP `mcp__claude_ai_Gmail__gmail_create_draft` | — |
| Gmail (SEND) | GitHub Actions `send_email.yml` (uses `PRI_OP_GMAIL_APP_PASSWORD`) | Python `smtplib.SMTP_SSL` |
| Gmail (filter create) | Python Gmail API (`sheets_token.json` has `gmail.settings.basic`) | — |
| Google Ads | MCP `google-ads` (read-only GAQL) | — |
| GitHub | `~/bin/gh` (auth'd as priihigashi) | — |
| Instagram | Composio MCP (only option) | — |
| Canva | MCP `mcp__claude_ai_Canva__*` | — |
| Vercel | MCP `mcp__vercel__*` | — |

**Deferred MCP tools** (Gmail, Calendar, Drive, Canva, Vercel): load schemas via ToolSearch before calling — they fail without it.

Gmail MCP can only DRAFT. To SEND, trigger `send_email.yml` via `gh workflow run`. Never tell Priscila "Gmail blocked" — there are 3 routes.

## Key IDs

| Thing | ID / Path |
|---|---|
| Main spreadsheet (Ideas & Inbox) | `1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU` |
| Content Queue / Blog | `1C1CAZ8lSgeVLSSCYIg-D9XPJcSLHyIOh1okKtvhZZQg` |
| Spreadsheet Hub | `1qDbO6JQX0cKbZ9rHjiM7a4U_p7OOddZ3k3Sp30JJoqo` |
| Flow Plans Tracker | `1fggy918FgPfnMQ-dzGQk2zx9uhi2_-uWXMKGW4MA47k` |
| Chat Logs folder (Drive) | `1qitnbz5_8tfZI2rnTogV1zLLLLOwFVCw` |
| Productivity & Routine folder | `1b8Cfc8lJhu5unDaxDQIdo4xdN6X7n1nS` |
| Productivity & Routine doc | `1wVBuNOuOufT8WP4KCrrlVbKWRmQZjKvqmia1soUEBZE` |
| Content Hub (Drive) | `1p7s2Q7kCxzKdvaVRFxSoYAQ-IG_NhTqq` |
| Content Creation (Drive) | `1um7y2Yt8zi9KGxev6kfFJYgrkMYwrCNh` |
| Content Formats registry (Doc) | `1XqXSyJC_iHMTrmMxpM5ZR7S-WQxz19HhDJO1HomdncM` |
| html to image folder | `1tE-2Ps8V8ZKQ4etyvzk47ZWyzeHAD2nk` (Marketing > Image Creation) |
| Service account (Sheets) | `oak-park-sheets@gen-lang-client-0364933181.iam.gserviceaccount.com` |

## Drive routing — topic drive + shortcut

Every file goes to its topic's **shared drive** (source of truth) with a **shortcut** in the working cross-ref folder. Never mix topics. Never upload to My Drive as final destination.

| Topic | Source-of-truth drive | Drive ID | Shortcut folder |
|---|---|---|---|
| Higashi / Hig Negócios / mom's site / Alexandra | Higashi Imobiliária - Claude | `0AN7aea2IZzE0Uk9PVA` | Website folder `1CKWTojSg2uQmXjNnKlAaSBCTfxtSQBvH` |
| OPC / Oak Park Construction | Oak Park Construction | `0AJp3Phs0wIBOUk9PVA` | TBD |
| News (Brazil/USA news niche) | News | `0AH7_C87G0ZwgUk9PVA` | TBD |
| Stocks / investing / Robinhood | Stocks | `0AF6S_f8PH2_aUk9PVA` | Originals - Stock (`1JFndBkUh6Bac6MD7JKgIns2xgO188b1T`) in Marketing |
| Content / marketing / McFolling / general | Marketing | `0AIPzwsJD_qqzUk9PVA` | n/a (self) |
| AI Content / AI-generated assets | AI Content | `0ACJVarTjgmFUUk9PVA` | TBD |
| UGC / creator clips | UGC | `0AEz0NlGr3tlLUk9PVA` | TBD |

Always include `supportsAllDrives=true`. Always include `includeItemsFromAllDrives=true` on list. Phone-initiated uploads into My Drive → route via `drive_route_file.yml`.

## Drive upload — BANNED methods vs correct method

⛔ **BANNED — silently creates empty files:**
1. `GOOGLEDRIVE_CREATE_FILE` with `content=...` (Composio)
2. `mcp__claude_ai_Google_Drive__create_file` with `content=...`
3. Any MCP `create_file` variant with `content=` / `file=` / `body=` bytes

MCP `create_file` is ONLY for empty folders (`mimeType: application/vnd.google-apps.folder`) or empty Docs to be filled via `GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN`. Never for file bytes.

✅ **CORRECT — OAuth Python `googleapiclient`:**

```python
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

creds = Credentials.from_authorized_user_file(
    '/Users/priscilahigashi/ClaudeWorkspace/Credentials/sheets_token.json')
drive = build('drive', 'v3', credentials=creds)
drive.files().create(
    body={'name': '<filename>', 'parents': ['<SHARED_DRIVE_FOLDER_ID>']},
    media_body=MediaFileUpload('<local_path>', mimetype='<mime/type>'),
    supportsAllDrives=True,
    fields='id,name,webViewLink',
).execute()
```

Full skill: `~/.agents/skills/drive-upload/SKILL.md`. After upload, VERIFY via `search_files` before reporting done.

## HTML → image — deterministic, never substitute

When Priscila says "turn this HTML into image", "convert to png", "export slides", "save the carousel":

- Use `/html-to-image` skill (`~/.agents/skills/html-to-image/SKILL.md`).
- Script: `node "/Users/priscilahigashi/ClaudeWorkspace/Content Templates/_Scripts/export_slides.js" "<input.html>" "<output_dir>"`
- Default Drive destination: Marketing > Image Creation > `html to image` (folder `1tE-2Ps8V8ZKQ4etyvzk47ZWyzeHAD2nk`).
- NEVER substitute OpenAI / Ideogram / Recraft / Seedream / Canva-AI / Nano-Banana — those are text-to-image AI and hallucinate a new design. Only HTML+Playwright and Remotion render exact design.
- Verify: file count = `.slide` count; every PNG ≥ 15KB; slide sizes differ.

## Avatar / talking-head tooling

- **Mike (OPC)** → **HeyGen** avatar. Has outdoor video footage uploaded.
- **Matt (OPC)** → **D-ID** (photos only — animates still with lip-sync).
- **Alexandra (Higashi) / new AI faces** → **Seedream 4.5** for person generation → **Nano Banana 2 (NB2)** for background edits only.
- Never ask NB2 to regenerate a person. Never ask Seedream to edit backgrounds on an existing face. Two-step pipeline is non-negotiable.

## Content Formats registry — read before producing content

File: `~/ClaudeWorkspace/_Master Plans & Docs/CONTENT_FORMATS.md` | Doc: `1XqXSyJC_iHMTrmMxpM5ZR7S-WQxz19HhDJO1HomdncM`

READ this before producing any carousel, reel, hook, or copy. WRITE to it when Priscila names a new format (same session). When she says "format" / "same style" / "like the X one" / "series" / "split screen" — check the file first.

## Capture — auto content-ideas rule

Every `/capture` of a video MUST auto-produce content ideas from the TOPICS, even if the clip itself is unused. Minimum output per capture: 1 carousel idea + 1 reel idea + 2–3 additional topic breakdowns. Topics = raw material. Posts = original. Never reuse the captured person's clip unless Priscila explicitly says to.

## Report format — every status update

```
✅ Done — specific action completed
🔴 Blocked — exact technical reason (one sentence)
⚠️ Only YOU can do — what / why / where / exact numbered steps (3+)
```

Never list "next steps" as unfinished work — either do it or explain the block. Asking Priscila = last resort. Tasks stay in flow until confirmed complete with evidence (cell ref, file path, run success).

## Before "Only YOU can do"

Check in order — if any apply, do it yourself:
1. YouTube URL → `youtube-transcript-api` (installed)
2. Instagram/TikTok URL → `/capture` skill
3. Tool/connection question → `reference_active_connections.md`
4. Spreadsheet 403 → share via OAuth token (see `reference_credentials.md`) — never ask Priscila

"Only YOU" = physical login OR content that was never provided. Nothing else.

## Script editing — non-negotiable

1. Read the full script first
2. List every ID/path/env-var referenced
3. Show what you are about to change + why before making any change
4. Preserve everything already working

Never rewrite a working script from scratch. Never assume a variable's value — verify.

## GitHub secrets — naming convention

All secrets in `priihigashi/oak-park-ai-hub` use a prefix identifying the account:
- `PRI_OP_` = Priscila / Oak Park Construction (priscila@oakpark-construction.com)
- `MCFOLLING_TOKEN` = McFolling / Airbnb inbox
- `SHEETS_TOKEN` = OAuth token for Sheets/Drive/Docs/Calendar/Gmail (all scopes)

When adding a new secret, use the correct prefix. Never generic names like `GMAIL_APP_PASSWORD`.

## Reusable skills — shared across Claude Code & Codex

Live in `~/.agents/skills/<name>/SKILL.md`. Symlinked into `~/.claude/skills/` and `~/.codex/skills/`.

| Skill | Use when |
|---|---|
| `session-start` | Beginning of chat / "morning" / "where did we leave off" |
| `session-exit` | "exit" / "closing" / "done for today" / "new chat" |
| `drive-upload` | Uploading any file to Drive (3-route fallback, banned methods listed) |
| `email-send` | Actually sending email (not just drafting) |
| `calendar-create` | Creating a Calendar event (3-route fallback) |
| `sheets-hub-log` | New spreadsheet created OR new tab added |
| `flow-plans-log` | New flow / master / process / niche doc created |
| `capture` | User drops URL / says capture / save / log / process this |
| `lessons-learned` | Mistake identified OR new rule established |
| `nano-banana-2` | Image generation (backgrounds / co-stars, never regenerate a person) |
| `html-to-image` | HTML carousel → PNG via Playwright (deterministic, never AI substitute) |
| `template-carousel` | Template systems for a niche/series (ask which tool first) |
| `sheets-automation` | Spreadsheet create / modify / batch-update |
| `remotion-best-practices` | Video composition via Remotion |
| `find-skills` | User asks "do we have a skill for X" |
| `thumbnail-generator` | Reel/video thumbnail for OPC brand |

Each SKILL.md specifies *when to use* and *when not to use*. Read the SKILL.md before executing.

## Session start — bypass mode prompt

After initial status report, ask exactly:

```
Bypass mode?
  Y = skip all approval prompts — I execute everything without asking
  N = I ask before risky actions (default)
  S = smart — I look at today's tasks and recommend a level
Reply Y / N / S
```

Safe to bypass (recommend Y): building/editing HTML, carousels, scripts, sites; reading Drive/Sheets/Calendar/GitHub; writing to Drive docs; running non-destructive workflows.

Do NOT bypass (recommend N): sending emails; deleting Drive files permanently; posting to Instagram/social; `git push --force`; financial / legal / client-facing docs; McFolling client data.

## Immediate-action rules — no task, just do it

- **She drops a URL** → save to Inspiration Library tab in Ideas & Inbox immediately, then create calendar task with the full URL + `/capture` instructions + purpose + Drive links.
- **She says "add a column" / "fix the spreadsheet"** → do it right now, confirm with cell reference. Do NOT create a task.
- **She says "add this to the plan"** → edit the plan doc directly, then update the Flow Plans Tracker row (`1fggy918FgPfnMQ-dzGQk2zx9uhi2_-uWXMKGW4MA47k`).
- **Calendar event creation** → never tell her to add it manually. Try MCP → Composio → Python OAuth. Only report blocked if all 3 routes fail.
- **Spreadsheet 403** → share via OAuth token using the service account `oak-park-sheets@gen-lang-client-0364933181.iam.gserviceaccount.com`. Never ask her to click share.

## Content categories + approval flow

Three types of content:
1. **Talking Head / Expert** — Mike on camera, under 1 min. 4AM agent finds topic.
2. **Project Progress / Before-After** — min 4 photos, or 2 photos for before-after only.
3. **Product Tips** — single image or carousel OK.

Three-step approval: idea → production → final → Buffer schedules.

## Key GitHub Actions workflows (priihigashi/oak-park-ai-hub)

| Workflow | Use |
|---|---|
| `capture_pipeline.yml` | `/capture` pipeline — URL → transcript → Inspiration Library |
| `send_email.yml` | Actually send email via `PRI_OP_GMAIL_APP_PASSWORD` |
| `avatar_generate.yml` | Seedream 4.5 person generation |
| `generate_image.yml` | Nano Banana 2 background generation / edits |
| `drive_route_file.yml` | Route phone-uploaded file from My Drive → topic drive + shortcut |
| `4am_agent.yml` | Nightly cron: scrape, classify, pattern-learn |
| `carousel_compare.yml` | Ideogram/Recraft carousel comparison (FORMAT-005) |

Trigger pattern: `~/bin/gh workflow run <name> --repo priihigashi/oak-park-ai-hub -f <key>="<value>"`. All workflows use `SHEETS_TOKEN` (never Composio) for Drive/Sheets.

## Session exit protocol

When she says "exit" / "closing" / "done for today":

1. **Chat log** → new Doc in Chat Logs folder (`1qitnbz5_8tfZI2rnTogV1zLLLLOwFVCw`) named `LOG_YYYY-MM-DD_HHMM`. Include: what was discussed, what shipped (files/cells/runs), what's carry-forward, Drive links. Keep last 7 days only.
2. **Productivity & Routine doc** (`1wVBuNOuOufT8WP4KCrrlVbKWRmQZjKvqmia1soUEBZE`) → mark completed tasks DONE, add new ongoing tasks from this session.
3. **Handoff** (if context near limit or session was long) → follow the handoff flow below.

Write via Composio `GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN` first, Python Docs API `batchUpdate` as fallback. Never tell her to type it manually.

## Context full / new chat handoff

When context is running out OR Priscila says "start new chat" / "context is full" / "closing":

1. Create doc `HANDOFF_YYYY-MM-DD` in Productivity & Routine folder (`1b8Cfc8lJhu5unDaxDQIdo4xdN6X7n1nS`).
2. Write via `GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN` — include: what was done (files, commits, runs, IDs), what is pending (priority ordered), errors + fixes, key IDs, which skill to invoke next.
3. Write standard chat log to Chat Logs folder (`1qitnbz5_8tfZI2rnTogV1zLLLLOwFVCw`).
4. Never leave a new chat cold — always write the handoff BEFORE context is exhausted.

## ADHD support — never lose an idea

When she starts a thought and connects to another idea mid-sentence, capture BOTH. Save the new idea to 📥 Inbox tab in Ideas & Inbox immediately before continuing. If she says "I had another idea", ask what it was before moving on.

Voice-to-text rule: parse all clear parts + decide. Ask MAX 1 question total. Never list 5 questions.

## Known repeat mistakes

See `~/.claude/CLAUDE.md` → "KNOWN REPEAT MISTAKES" (15+ documented).

Top 5 to internalize:
1. Never say "I may not have access" — check `reference_active_connections.md` first.
2. Never use markdown tables in `GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN` — causes 400 error.
3. Drive uploads to shared folders MUST include `supportsAllDrives=true` or return 404.
4. Deferred MCP tools (Gmail, Calendar, Drive, Canva, Vercel) need ToolSearch schema load before first call.
5. Never guess which spreadsheet a GitHub secret points to — stop and ask. Document confirmed mappings in `reference_credentials.md`.

## Full ruleset

For Claude Code: `~/.claude/CLAUDE.md` is authoritative (covers session start/exit, bypass modes, ADHD support, Spreadsheet Hub rule, Flow Plans Tracker rule, 4AM agent architecture, AIOX audit requirement, etc.).

For any AI CLI starting fresh: read this AGENTS.md, then `~/.claude/CLAUDE.md`, then `MEMORY.md`, then `reference_active_connections.md`.

## Every session — read-and-align protocol

At the start of any session:
1. Read this AGENTS.md and the AGENTS.md in the working repo if different.
2. Read the SKILL.md of any skill you plan to invoke.
3. **Automation inventory check** — before touching a workflow, script, secret, or Drive folder, list every existing automation that touches that surface area (GitHub Actions under `.github/workflows/`, skills under `~/.agents/skills/`, scripts under `~/ClaudeWorkspace/_Scripts/` or `scripts/`). If you can't enumerate what's already there, you haven't read enough — stop and read.
4. Summarize the active rules, flag any conflicts with the task at hand, and propose the next 3 safest steps before editing anything.

Do not claim to save anything to persistent memory — Codex has none. AGENTS.md, CLAUDE.md, SKILL.md files, and the `priihigashi/oak-park-ai-hub` repo are the only durable source of truth.

---

## 2026-04-18 — Capture pipeline audit & fixes

### What we found (problems)

**Dual pipeline confusion**
- Two email subjects existed ("SOVEREIGN capture done" and "Capture done") — appeared to be different scripts but is ONE script (`capture_pipeline.py`) with two project modes
- Naming was inconsistent: queue used `brazil`/`usa`, pipeline used `sovereign`/`content` — manual trigger would crash if `brazil` was typed

**Capture queue failures (root causes)**
- `instaloader` was never installed in the GitHub Actions runner — the fallback bypassing Instagram's `shared_data` block was silently skipped every time
- Apify actor_id format was wrong: `apify/instagram-scraper` should be `apify~instagram-scraper` — Apify REST API returns 404 with slash format
- Manual captures never marked their queue row as done → 6AM queue processor retried the same URL, hit Instagram rate limits, and failed
- 10 rows stuck with ⚠️ failures as a result

**YouTube captures**: GitHub runner IPs are blocked by YouTube — not fixable in pipeline code, needs residential proxy or non-cloud runner

### What we fixed

- **Renamed**: SOVEREIGN → "news" project, content → "opc" project (legacy names still work)
- **Email subjects**: "News capture done — NWS-..." and "OPC capture done — niche | ..."
- **Installed instaloader** in `capture_queue.yml` pip install step
- **Fixed Apify actor_id**: `apify/instagram-scraper` → `apify~instagram-scraper`
- **Added `_mark_queue_processed(url)`**: called at end of all 3 `run_*()` functions — marks queue row TRUE so 6AM run skips it
- **Added `retry_failed` input**: triggers retry of all ⚠️ rows in one run
- **Added `bulk_urls` input**: paste newline-separated URLs, adds + processes immediately

### Features ported between pipelines

**OPC → News (things News was missing):**
- Bilingual content brief (EN + PT-BR doc in Drive folder)
- Inspiration Library row written on every capture
- Topic cluster scraper triggered on capture

**News → OPC (things OPC was missing):**
- SRT captions generated for non-YouTube captures

### Side-by-side pipeline comparison

| Feature | News (`run_news`) | OPC (`run_opc`) |
|---|---|---|
| Story ID prefix | NWS- | CNT- |
| Drive destination | SOVEREIGN_FOLDER_ID | Content Hub + Content Creation |
| Sheet written | Calendar + Inspiration Library | Inspiration Library + Ideas Queue |
| Bilingual brief | ✅ EN + PT-BR docs | ✅ EN + PT-BR docs (added) |
| SRT captions | ✅ | ✅ (added) |
| Topic cluster scraper | ✅ | ✅ |
| Inspiration Library row | ✅ (added) | ✅ |
| Image concept | Person/event photo (politician, location) | Property/lifestyle photo |
| Queue dedup | ✅ (added) | ✅ (added) |

### Commits
- `12ad1c1` — Capture Queue fix (instaloader, Apify actor_id, retry_failed, bulk_urls)
- `fc557dd` — Pipeline rename + feature sync + dedup fix
- `eabf921` — PT translation wired into capture pipeline

### Pending
- Run retry: Actions → Capture Queue Processor → `retry_failed=true` → `max_per_run=10`
- YouTube failures still need residential proxy / non-cloud runner solution
- Monitor if instaloader fix holds for the 10 stuck rows
