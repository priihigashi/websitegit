# AGENTS.md — Priscila's Root Instructions for Codex & Other AI CLIs

> Source of truth: `priihigashi/oak-park-ai-hub` (GitHub). Local mirror at `~/AGENTS.md` + `~/.claude/CLAUDE.md`.
> For Claude Code: full rules live in `~/.claude/CLAUDE.md` (437 lines).
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

## Connection routing — Cloud first, Composio fallback

| Service | Primary | Fallback |
|---|---|---|
| Sheets | Google Sheets API via OAuth (SHEETS_TOKEN) | Composio `GOOGLESHEETS_*` |
| Docs (write) | Composio `GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN` only | — |
| Drive | MCP `mcp__claude_ai_Google_Drive__*` | OAuth Python + `supportsAllDrives=true` |
| Calendar | MCP `mcp__claude_ai_Google_Calendar__*` (deferred — load schema first) | Python OAuth |
| Gmail (draft) | MCP `mcp__claude_ai_Gmail__gmail_create_draft` | — |
| Gmail (send) | GitHub Actions `send_email.yml` | Python smtplib with `PRI_OP_GMAIL_APP_PASSWORD` |
| Google Ads | MCP `google-ads` (read-only GAQL) | — |
| GitHub | `~/bin/gh` (auth'd as priihigashi) | — |
| Instagram | Composio MCP (only option) | — |
| Canva | MCP `mcp__claude_ai_Canva__*` | — |

**Deferred MCP tools** (Gmail, Calendar, Drive, Canva): load schemas via ToolSearch before calling — they fail without it.

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
| Marketing shared drive | `0AIPzwsJD_qqzUk9PVA` |
| Higashi shared drive | `0AN7aea2IZzE0Uk9PVA` |
| Hig Negócios website folder | `1CKWTojSg2uQmXjNnKlAaSBCTfxtSQBvH` |
| Service account (Sheets) | `oak-park-sheets@gen-lang-client-0364933181.iam.gserviceaccount.com` |

## Drive routing — check before every file operation

- Higashi / Hig Negócios / mom's site → Higashi shared drive (`0AN7aea2IZzE0Uk9PVA`)
- OPC / Oak Park / McFolling / content / marketing → Marketing shared drive (`0AIPzwsJD_qqzUk9PVA`)
- Never mix. Never upload to My Drive. Always include `supportsAllDrives=true`.

## Report format — every status update

```
✅ Done — specific action completed
🔴 Blocked — exact technical reason (one sentence)
⚠️ Only YOU can do — what / why / where / exact numbered steps (3+)
```

Never list "next steps" as unfinished work — either do it or explain the block. Asking Priscila = last resort.

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

## Reusable skills — shared across Claude Code & Codex

Live in `~/.agents/skills/<name>/SKILL.md`. Symlinked into `~/.claude/skills/` and `~/.codex/skills/`.

| Skill | Use when |
|---|---|
| `session-start` | Beginning of chat / "morning" / "where did we leave off" |
| `session-exit` | "exit" / "closing" / "done for today" / "new chat" |
| `drive-upload` | Uploading any file to Drive (3-route fallback) |
| `email-send` | Actually sending email (not just drafting) |
| `calendar-create` | Creating a Calendar event (3-route fallback) |
| `sheets-hub-log` | New spreadsheet created OR new tab added |
| `flow-plans-log` | New flow / master / process / niche doc created |
| `capture` | User drops URL / says capture / save / log / process this |
| `lessons-learned` | Mistake identified OR new rule established |
| `nano-banana-2` | Image generation (backgrounds / co-stars, never regenerate a person) |
| `sheets-automation` | Spreadsheet create / modify / batch-update |
| `remotion-best-practices` | Video composition via Remotion |
| `find-skills` | User asks "do we have a skill for X" |
| `thumbnail-generator` | Reel/video thumbnail for OPC brand |

Each SKILL.md specifies *when to use* and *when not to use*. Read the SKILL.md before executing.

## Known repeat mistakes (read before every session)

See `~/.claude/CLAUDE.md` → "KNOWN REPEAT MISTAKES" (15+ documented).

Top 3 to internalize:
1. Never say "I may not have access" — check `reference_active_connections.md` first.
2. Never use markdown tables in `GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN` — causes 400 error.
3. Drive uploads to shared folders MUST include `supportsAllDrives=true` or return 404.

## Full ruleset

For Claude Code: `~/.claude/CLAUDE.md` is authoritative (437 lines, covers session start/exit, bypass modes, ADHD support, spreadsheet hub rule, flow plans tracker rule, 4AM agent architecture, AIOX audit requirement, etc.).

For any AI CLI starting fresh: read this AGENTS.md, then `~/.claude/CLAUDE.md`, then `MEMORY.md`, then `reference_active_connections.md`.
