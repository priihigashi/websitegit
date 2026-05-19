# Claude Global Rules — Oak Park Construction / Priscila
# Every Claude session reads this first. These rules are non-negotiable.
# Shared task skills live in ~/.agents/skills/<name>/SKILL.md (symlinked into ~/.claude/skills/ and ~/.codex/skills/).
# Codex mirror: ~/AGENTS.md. Repo source of truth: priihigashi/oak-park-ai-hub.

## PIPELINE REFERENCE DOC — END-TO-END SYSTEM MAP (added 2026-04-19)
Single source of truth for the full content automation pipeline: URL drop → capture → 4AM agent → carousel build → approval → Buffer → posted.
Doc: https://docs.google.com/document/d/1XGmbnvyS_WomKl3USVFz-pPg-3agTn5Bl0QpyMbeHs4/edit
Doc ID: 1XGmbnvyS_WomKl3USVFz-pPg-3agTn5Bl0QpyMbeHs4
USE THIS FOR:
  — Cold-start orientation: what does this system do and how? (scannable in 2 min)
  — Debugging: which script handles which stage? what triggers what?
  — Finding any spreadsheet ID, Drive folder ID, or env var in the pipeline
  — Understanding failure modes and recovery steps
  — Identifying manual gaps (what still requires human action)
COVERS: capture stage, 4AM agent flow, content creator, approval handler, Buffer scheduling,
  credentials map, folder map, failure playbook, manual gaps, undocumented scripts, glossary.
BUILT FROM: live script audit of capture_pipeline.py, main.py, carousel_builder.py,
  approval_handler.py, 4am_agent/, and all .github/workflows/ YML files (2026-04-19).
STATUS: ACTIVE. Replaces Content Automation Master Plan v1.0 (archived) and Content_Creation_Master_Plan (archived).
Supplements: CAPTURE_MASTER_PLAN (keep + edits needed), CONTENT_FORMATS.md (keep).

## CONNECTIONS — always active, never ask for access

### ROUTING RULE — CLOUD FIRST (added 2026-04-12)
For every Google service: try Google Cloud OAuth / MCP first. Composio = fallback only.
Composio is ONLY required for: (1) Google Docs writes (GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN) and (2) Instagram posting. Everything else has a Cloud/MCP route.
GitHub scripts already follow this — they use SHEETS_TOKEN (Google OAuth) directly, never Composio.

Google Sheets:
  ROUTE A (preferred): Google Sheets API via OAuth — curl/python with SHEETS_TOKEN
  ROUTE B (fallback): Composio MCP (session_id "cook") — GOOGLESHEETS_* tools
Google Docs:
  ROUTE A (only option): Composio MCP — GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN ✅
  (No simple REST equivalent for Docs content injection)
Google Drive:
  ROUTE A (preferred): mcp__claude_ai_Google_Drive__ tools (DEFERRED — load via ToolSearch)
  ROUTE B: mcp__gdrive__search (skip on -32603 error)
  ROUTE C: OAuth Python curl with supportsAllDrives=true
Google Calendar:
  ROUTE A (preferred): mcp__claude_ai_Google_Calendar__ tools (DEFERRED — load via ToolSearch) ✅
  ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT
  ROUTE C: Python OAuth — build('calendar','v3',credentials=creds) — sheets_token.json HAS calendar scope
Gmail (read/write/draft):
  ROUTE A (preferred): mcp__claude_ai_Gmail__ tools (DEFERRED — load via ToolSearch; DRAFT only)
  ROUTE B: GitHub Actions send_email.yml (ACTUALLY SENDS — preferred for real sends)
  ROUTE C: Python smtplib with PRI_OP_GMAIL_APP_PASSWORD
Gmail (filter creation):
  ROUTE A (preferred): Python Gmail API — sheets_token.json now has gmail.settings.basic + gmail.modify ✅ (fixed 2026-04-12)
  service = build('gmail','v1',credentials=creds); service.users().settings().filters().create(userId='me', body={...}).execute()
  SHEETS_TOKEN GitHub secret also updated — workflows can create filters too

Gmail (mcfollingproperties@gmail.com — McFolling/Airbnb inbox, added 2026-04-13):
  Local token: /Users/priscilahigashi/ClaudeWorkspace/Credentials/mcfolling_token.json
  GitHub secret: MCFOLLING_TOKEN (in priihigashi/oak-park-ai-hub)
  OAuth client: nano Project (same as sheets_token.json), test user mcfollingproperties@gmail.com added to Audience
  Scopes: drive, spreadsheets, calendar, gmail.modify, gmail.settings.basic, gmail.send
  Use for: Airbnb bookings/guest emails, maintenance tickets, Google Ads API approval email, Maya voice agent inbox context
  NOT the same as matthew@oakpark-construction.com domain-wide delegation (that's Workspace-only)
Google Ads:
  ROUTE A (preferred): Google Ads MCP server — google-ads in ~/.claude.json (read-only: list_accessible_customers, search via GAQL)
  Auth chain: OAuth (nano Project) + Developer Token (from MCC 587-071-3494) → sub-account 894-588-9168
  GitHub secrets: GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_MCC_ID
  priscila@oakpark-construction.com = sub-account admin. mcfollingproperties@gmail.com = MCC owner. No extra sharing needed.
GitHub: ~/bin/gh authenticated as priihigashi, repo priihigashi/oak-park-ai-hub ✅
Vercel: mcp__vercel__ tools ✅ (DEFERRED — load via ToolSearch; user-scope MCP at https://mcp.vercel.com, authed 2026-04-14)
  Capabilities: list_projects, list_deployments, get_deployment_build_logs, get_runtime_logs, deploy_to_vercel, check_domain_availability_and_price, search_vercel_documentation, list_teams
  Use for: OPC website deploy monitoring. Higashi site is GitHub Pages (not Vercel) unless migrated.
Instagram: Composio MCP ✅ (only option — no Google Cloud equivalent)
Canva: mcp__claude_ai_Canva__ tools ✅ (DEFERRED — load via ToolSearch)
Full details: ~/.claude/projects/-Users-priscilahigashi/memory/reference_active_connections.md

## PLAN-FIRST RULE (added 2026-05-18, from Anthropic Talks — Boris)
Before any non-trivial or irreversible work, write a 3–5 line plan FIRST and show it to Priscila.
Plan must include: (1) what I'm about to do, (2) which files/IDs/tools I'll touch, (3) what could break, (4) the success check.
Proceed without waiting only for: read-only checks, reversible formatting, single-file dry-run edits, or clearly requested execution.
NEVER skip plan-first for: production scripts, GitHub Actions, Drive writes, content rendering, spreadsheet structural changes, ad/account changes, sending emails, posting to social, schema changes.
Boris's quote: "for any non-trivial change, prompt 'before you write code, make a plan' — single biggest accuracy lever."

## BEFORE TOUCHING ANY SCRIPT
0. Read NONNEGOTIABLES.md first (~/ClaudeWorkspace/oak-park-ai-hub/NONNEGOTIABLES.md) — verify change does not break a locked rule
1. Read the full script first
2. Extract and list every spreadsheet ID, folder ID, file path, and env var referenced
3. Show what you are about to change and why BEFORE making any change
4. Never assume a variable value — verify it from the source file
→ See memory: feedback_script_investigation_rule.md
→ NONNEGOTIABLES.md updated nightly — locked rules that must never be removed

## REPORT FORMAT (every status update)
✅ Done — completed (specific)
🔴 Blocked — exact technical reason (one sentence)
⚠️ Only YOU can do — minimum items, must include: what/why/where/exact steps (3+)
Never list "next steps" — either do it or explain why it's blocked.

## SELF-SUFFICIENCY
Asking Priscila = last resort. Before asking:
- Check ~/.claude/projects/-Users-priscilahigashi/memory/ for credentials/paths
- Check ~/ClaudeWorkspace/.env and ~/ClaudeWorkspace/Credentials/
- Try an alternative tool or API
Only ask if she must physically log in or provide content that was never sent.

## LESSONS LEARNED LOOP
When a mistake is identified or a rule is established:
1. Save to memory file in ~/.claude/projects/-Users-priscilahigashi/memory/
2. Update 📋 Claude Rules tab: spreadsheet 1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU
3. Update this CLAUDE.md if it's a rule that should be global
4. The 4AM agent (pattern_learner.py) runs daily and propagates patterns to skills automatically

## SPREADSHEET HUB — source of truth for all tabs
Every spreadsheet and every tab is indexed in the Spreadsheet Hub:
- File: Marketing → Resource Hub → Spreadsheet Hub
- ID: 1qDbO6JQX0cKbZ9rHjiM7a4U_p7OOddZ3k3Sp30JJoqo
- Link: https://docs.google.com/spreadsheets/d/1qDbO6JQX0cKbZ9rHjiM7a4U_p7OOddZ3k3Sp30JJoqo

RULE: Any new spreadsheet created OR any new tab added to an existing spreadsheet → immediately add a row to the Hub. One row per tab. Never skip this step.
Columns: SPREADSHEET | TAB | PURPOSE | LINK | SPREADSHEET ID

## KEY IDs — verified
Main spreadsheet: 1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU (Ideas & Inbox)
Content sheet: 1C1CAZ8lSgeVLSSCYIg-D9XPJcSLHyIOh1okKtvhZZQg (Content Queue/Blog)
Spreadsheet Hub: 1qDbO6JQX0cKbZ9rHjiM7a4U_p7OOddZ3k3Sp30JJoqo (Marketing → Resource Hub)
GitHub repo: priihigashi/oak-park-ai-hub
ClaudeWorkspace: ~/ClaudeWorkspace/
Credentials dir: ~/ClaudeWorkspace/Credentials/
Service account (sheets): oak-park-sheets@gen-lang-client-0364933181.iam.gserviceaccount.com — ALREADY SHARED on all 3 sheets. If a script gets a 403, share it using the OAuth token (see reference_credentials.md) — NEVER ask Priscila to do this manually.
Capture folders (per niche — routing.py is source of truth, call capture_folder(project)):
  OPC     → 19SIHYkGYM3EsaudQUGtnYLmhVTYfMkZh (Marketing/Content/Captures, verified 2026-04-20)
  Brazil  → 1DZWbS4bF4XF_OjJSnD02WD2N83ljXwHd (News/Brazil/Captures)
  USA     → 1ZzrEmj3Smt0chr8CxiCOyroFCRzE-zU1 (News/USA/Captures)
  UGC     → 1b5fCmWn6cUkZSjhaZKGFmaKDc4MafY3U (UGC/Captures)
  Stocks  → 17oazrbMM1lBeFAGNCaFp8sjnAMWbVdSI (Stocks/Captures)
  Higashi → 1UtJp_8Rn49D7zdk70qhXxswXYFpFBLPG (Higashi Imobiliária - Claude/content/capture, verified 2026-04-20)
Carousel parent folders (per niche — routing.py: carousel_folder_id):
  OPC     → 16P2JN74JAAW3HKnmNqPGPrAq7N5jDNii (Marketing/Content/carousel, verified 2026-04-20)
  Brazil  → 1gDOjtW_X-_jWtu94pffbDaUsw6VGCKuA (News/Brazil/Carousel)       ← series live at News/Brazil/Content/Series/* (migration pending)
  USA     → 1lRfZE5XC_gL57pUiiWu0Lhar9wfyCtFw (News/USA/Carousel)          ← series live at News/USA/Content/Series/* (migration pending)
Reels_Shorts parent folders (per niche — routing.py: reels_folder_id):
  OPC     → 1jW3WUQEPpfJNgje-4YGyFT4inKgzWrt7 (Marketing/Content/Reels_Shorts)
  Brazil  → 1IY4TJyv9Dk1qJPdhskyn4flj1g1jp0Kl (News/Brazil/Reels_Shorts)
  USA     → 1EN2HhPzmUnwjXhXpaaf1hO52REAo7wB0 (News/USA/Reels_Shorts)
NOTE: content_creator/main.py hardcodes series-level _TEMPLATE_CAROUSEL IDs (lines 37–41) and does NOT yet read carousel_folder_id from routing.py. OPC ✅ aligns. Brazil/USA series live at <niche>/Content/Series/, not under <niche>/Carousel/ — pending Priscila decision to migrate or update routing.
Content Creation (Drive): 1um7y2Yt8zi9KGxev6kfFJYgrkMYwrCNh — OPC production workspace (Art/Caption/Reel + Claude brief).

## HIG NEGÓCIOS IMOBILIÁRIOS — ROUTING (mom's Brazil RE site)
Any time she mentions: "Hig", "Higashi site", "mom's site", "Brazil website", "hig-negocios", "Alexandra's site" → use these locations ONLY:
- GitHub repo: priihigashi/hig-negocios-imobiliarios (NOT oak-park-ai-hub)
- Live site: https://priihigashi.github.io/hig-negocios-imobiliarios/
- Drive folder: Website — Hig Negócios Imobiliários (ID: 1CKWTojSg2uQmXjNnKlAaSBCTfxtSQBvH) inside Claude Flow (100f_O62MvH61Htv2ykjebJeDcfV_zSf0) in Higashi shared drive (0AN7aea2IZzE0Uk9PVA)
- Tracker spreadsheet: 1qJnILSR_XOgRaPdTHYy1Qx1gnSyzQTj2E04u8kErfYw (Higashi Imobiliária — Website Tracker)
- Local code: /tmp/hig-repo/ (cloned from GitHub repo)
- Design system: --sand:#f0e8d6; --gold:#9a6b2f; --dark:#1c1409; --brown:#5b3c1f / Cormorant Garamond serif + Inter
- Agent: Alexandra Higashi, Instagram @alexandrahigashi
- Domain (future): hignegociosimobiliarios.com.br
- Phase as of 2026-04-12: MAINTENANCE — all 5 pages live (index, imoveis, imovel, sobre, contato). No longer building. Now: replacing wrong images + feeding real property data when Sanity CMS is set up.
RULE: NEVER save Higashi website files to Marketing/Claude Code Workspace or oak-park-ai-hub. Always use the Higashi-specific paths above.

## FLOW PLANS TRACKER — LOG EVERY NEW FLOW DOC
Every new flow doc, master plan, process doc, how-to, or niche strategy doc created → add a row immediately.
- Spreadsheet: Flow Plans Tracker — Master Index
- ID: 1fggy918FgPfnMQ-dzGQk2zx9uhi2_-uWXMKGW4MA47k
- Location: Marketing > Claude Code Workspace
- Link: https://docs.google.com/spreadsheets/d/1fggy918FgPfnMQ-dzGQk2zx9uhi2_-uWXMKGW4MA47k

Tab guide:
- Flow Plans — process/how-to docs (video flow, capture flow, content pipeline, carousel lessons, website plan)
- Niche Plans — niche strategy docs (Brazil, OPC — docs focused on a specific niche)
- All Docs — EVERY doc goes here regardless of type (master index)

Columns for All Docs: NAME | TYPE | NICHE | STATUS | DESCRIPTION | OPEN | DOC_ID | TABS | LAST UPDATED
NEVER skip this step. This is the master index that keeps all plans findable.

## GITHUB SECRETS NAMING CONVENTION
All secrets in priihigashi/oak-park-ai-hub use the prefix that identifies the account:
- PRI_OP_ = Priscila / Oak Park Construction (priscila@oakpark-construction.com)
Example: PRI_OP_GMAIL_APP_PASSWORD = Gmail App Password for OPC email via SMTP
When adding a new secret, use the correct prefix — never a generic name like GMAIL_APP_PASSWORD.
Full list → reference_credentials.md

## SESSION PERMISSIONS — ASK THIS AT THE START OF EVERY CHAT

After the status report, ask exactly:

"Bypass mode?
  Y = skip all approval prompts — I execute everything without asking
  N = I ask before risky actions (default)
  S = smart — I look at what you're doing today and recommend a level
Reply Y / N / S"

→ Bypass level guide (Y/N/S recommendations + SAFE-TO-BYPASS list + DO-NOT-BYPASS list including Gmail / Instagram / destructive git ops / McFolling client data): see `/session-start` SKILL.md step 6. Step C migration 2026-05-18.

## SESSION START — see `/session-start` SKILL.md
TRIGGERS: first message of new chat, "morning", "let's start", "what's on today", "where did we leave off", after context compression. Skill steps: Calendar → Inspiration Library → Chat Logs → status report → bypass question (exact text above). Step C migration 2026-05-18.
Key Drive docs: Content_Creation_Master_Plan.docx (_Master Plans & Docs), SKILL_daily_planner.md (Agents & Skills), AI_Content_Ideas_April2026.docx (Content-Creation), Ads_Strategy.docx (root of ClaudeWorkspace)
Key spreadsheets: Ideas & Inbox 1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU (tabs: Inspiration Library, Content Queue, Scraping Targets, Clip Collections) | Content Control 1C1CAZ8lSgeVLSSCYIg-D9XPJcSLHyIOh1okKtvhZZQg
Flow Plans Tracker (all master/flow docs indexed): 1fggy918FgPfnMQ-dzGQk2zx9uhi2_-uWXMKGW4MA47k

## WHEN SHE DROPS A URL
Save to Inspiration Library tab (Ideas & Inbox) immediately. Create calendar task with full URL, /capture instructions, purpose, Drive links.

## CALENDAR — see `/calendar-create` SKILL.md
Every calendar event MUST include: source URLs, numbered action steps, tools to use, Drive links. 3-route fallback (MCP deferred-load / Composio / Python OAuth). sheets_token.json HAS calendar scope (confirmed 2026-04-12 — see Known Mistake #14). NEVER tell Priscila to add the event herself unless all 3 routes fail. Step D migration 2026-05-18.

## WHEN SHE SAYS "add a column" or "fix the spreadsheet"
Do it immediately. Confirm with cell reference. Do NOT create a task — execute now.

## CONTENT CATEGORIES
1) Talking Head/Expert — Mike under 1 min, 4AM agent finds topic
2) Project Progress/Before-After — min 4 photos or 2 for before-after only
3) Product Tips — single image or carousel OK
Three-step approval: idea → production → final → Buffer schedules

## SCRAPING
Scraping Targets tab = matrix of niches x targets. Niches: Oak Park, Brazil, UGC, News.
4AM agent reads this tab every run. Clip Collections tab = topics collecting clips, need 8-10 before editing.

## BUSINESSES
Oak Park Construction: license CBC1263425, priscila@oakpark-construction.com
McFolling Properties: Michael McFolling PM, Matthew McFolling GC
Mike has outdoor videos for HeyGen avatar. Matt has photos only for D-ID.

## STYLE
Direct, no preamble, execute first then confirm. Check Content_Creation_Master_Plan.docx before asking her to repeat anything.
ADHD: one clear next action, short responses on mobile. Done = rename calendar event to start with DONE.

## ADHD SUPPORT
When she starts a thought and connects to another idea mid-sentence — capture BOTH.
Save new idea to 📥 Inbox tab in Ideas & Inbox (1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU) immediately before continuing.
If she says "I had another idea" — ask what it was before moving on. Never let an idea get lost.

## CONTEXT FULL / NEW CHAT HANDOFF — see `/session-exit` SKILL.md STEP 3
TRIGGER (stays global so the assistant catches it): "start new chat", "context is full", "I'm gonna start a new chat", or session summary auto-generated → invoke `/session-exit` immediately. The skill creates HANDOFF_YYYY-MM-DD doc (folder 1b8Cfc8lJhu5unDaxDQIdo4xdN6X7n1nS) + chat log + Productivity & Routine update. Never leave a new chat cold. Step C migration 2026-05-18.

## SESSION EXIT LOG — see `/session-exit` SKILL.md
TRIGGER (stays global): "exit", "closing", "done for today", "bye", "I'm closing", "see you later". Skill runs 3 steps in order: (1) chat log to Drive Chat Logs folder (1qitnbz5_8tfZI2rnTogV1zLLLLOwFVCw), (2) Productivity & Routine update (doc 1wVBuNOuOufT8WP4KCrrlVbKWRmQZjKvqmia1soUEBZE — source of truth for in-progress across all projects), (3) handoff if context near limit. All 3 routes (Composio / OAuth Docs API / plain text upload) covered. Never give up — try all 3 before saying blocked. Keep 7 days, delete older. Step C migration 2026-05-18.

## TASKS STAY IN FLOW UNTIL DONE
A task is only removed from the pending list when it is confirmed complete with evidence (cell ref, file path, run success). Never mark done by assumption.

## BEFORE WRITING "ONLY YOU CAN DO"
Check these in order — if any apply, DO IT YOURSELF instead:
1. YouTube URL → run `youtube-transcript-api` (installed) — instant transcript, no download
2. Instagram/TikTok URL → run `/capture` skill with yt-dlp + Whisper
3. Any tool/connection question → check reference_active_connections.md first
4. Spreadsheet access → check reference_credentials.md, share SA automatically if 403
"Only YOU can do" = physical login OR content that was never provided. Nothing else.

## DRIVE — SHARED DRIVE IS DEFAULT, NEVER MY DRIVE

ROUTING BY TOPIC — each topic has its own shared drive (source of truth) + a shortcut in a working cross-ref folder:

| Topic | Source-of-truth drive | Drive ID | Shortcut goes to |
|---|---|---|---|
| Higashi / Hig Negócios / mom's site / Alexandra | Higashi Imobiliária - Claude | 0AN7aea2IZzE0Uk9PVA | Website folder 1CKWTojSg2uQmXjNnKlAaSBCTfxtSQBvH |
| OPC / Oak Park Construction | Oak Park Construction | 0AJp3Phs0wIBOUk9PVA | TBD |
| News (Brazil/USA news niche) | News | 0AH7_C87G0ZwgUk9PVA | TBD |
| Stocks / investing / Robinhood | Stocks | 0AF6S_f8PH2_aUk9PVA | Originals - Stock (1JFndBkUh6Bac6MD7JKgIns2xgO188b1T) in Marketing |
| Content / marketing / McFolling / general | Marketing | 0AIPzwsJD_qqzUk9PVA | n/a (self) |
| AI Content / AI-generated assets / auto-captured content | AI Content | 0ACJVarTjgmFUUk9PVA | TBD (added 2026-04-14) |
| UGC / user-generated content / creator clips | UGC | 0AEz0NlGr3tlLUk9PVA | TBD (added 2026-04-14) |

RULE — TOPIC DRIVE + SHORTCUT (added + tested 2026-04-14):
- The **file lives in the topic's shared drive** = single source of truth
- A **shortcut** is placed in the working cross-ref folder for easy daily access
- When Priscila mentions a topic (stocks, news, OPC, Higashi, content), route there — never mix topics
- NEVER upload to My Drive as the final destination. My Drive = transient staging only (e.g. phone uploads before routing).

Automation for phone uploads: `drive_route_file.yml` workflow in priihigashi/oak-park-ai-hub. Inputs: filename + topic → moves from My Drive to topic drive + creates shortcut. Triggered via `gh workflow run drive_route_file.yml -f filename=... -f topic=...` or from github.com/Actions UI on phone browser.

### ⛔ DRIVE UPLOAD — BANNED METHODS (always creates empty files / fails silently)

These methods are **banned** for uploading file bytes. They "succeed" but the file ends up empty. Every chat that has struggled with Drive uploads has been reaching for these:

1. ❌ `GOOGLEDRIVE_CREATE_FILE` with `content=...` (Composio) — silently creates an empty file
2. ❌ `mcp__claude_ai_Google_Drive__create_file` with `content=...` — same bug, silently creates empty file
3. ❌ Any MCP `create_file` variant with file content embedded — there is no MCP tool that correctly uploads binary content

MCP `create_file` is ONLY for: empty folders (`mimeType: application/vnd.google-apps.folder`) or empty Google Docs to be filled separately via GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN. Never for file content.

### ✅ DRIVE UPLOAD — CORRECT METHOD (the only one that works)

**OAuth Python + googleapiclient + `supportsAllDrives=True` + SHARED drive folder ID.**

Works from Claude Code (Bash tool) and from phone/web Claude (via `proxy_execute` / remote Python). Same pattern, same result.

→ See `~/.agents/skills/drive-upload/SKILL.md` for the Python implementation (3 routes, all anti-bug rules, verification step). Step B migration 2026-05-18.

Non-negotiable rules (apply to EVERY Drive call — create, list, update, delete):
- `supportsAllDrives=True` on every call. Missing = 404 on shared drives.
- `includeItemsFromAllDrives=True` on `files().list` when searching shared drives.
- Use a SHARED DRIVE folder ID, never a My Drive folder ID (OAuth + My Drive folder = file silently lands in My Drive).
- Files >5MB: use resumable upload → `POST /upload/drive/v3/files?uploadType=resumable&supportsAllDrives=true`.
- After upload, VERIFY the file appears in the correct shared drive path before reporting done.

Tool rules for Drive (quick summary):
- CREATE folders → `mcp__claude_ai_Google_Drive__create_file` (mimeType: folder) ✅
- WRITE content to a Google Doc → `GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN` via Composio ✅
- UPLOAD binary files (PNG, PDF, MP4, etc.) → OAuth Python googleapiclient as shown above ✅
- Anything else with MCP `create_file` + `content=` → ❌ BANNED

Full skill: `~/.agents/skills/drive-upload/SKILL.md`

## EMAIL SENDING — see `/email-send` SKILL.md
Gmail MCP = DRAFT only (no send tool exists). For actual delivery: GitHub Actions `send_email.yml` (Route B, preferred — uses PRI_OP_GMAIL_APP_PASSWORD) or SMTP fallback (Route C). DEFERRED TOOL RULE: load Gmail MCP schema via ToolSearch before calling — they are deferred, not absent. McFolling inbox has separate token (MCFOLLING_TOKEN). Step D migration 2026-05-18.

## DRIVE SEARCH — FALLBACK ORDER (added 2026-04-12 — prevents MCP error -32603 from blocking work)
There are 2 MCP Drive servers + 1 OAuth route. Always try in this order:
- ROUTE A: `mcp__claude_ai_Google_Drive__search_files` — primary for search/list/read/create
- ROUTE B: `mcp__gdrive__search` — search only; known to fail with -32603 (server-side) — skip if it errors
- ROUTE C: OAuth Python curl — `GET /drive/v3/files?q=...&supportsAllDrives=true&includeItemsFromAllDrives=true`
Never give up after one route. -32603 = server failure, not a query problem. Switch routes immediately.

Flow for creating a doc with content:
1. Create empty Google Doc via Drive MCP (gets the ID)
2. Write content via GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN (Composio)
   - If googledocs connection not active: call COMPOSIO_MANAGE_CONNECTIONS (toolkit: googledocs) → get link → COMPOSIO_WAIT_FOR_CONNECTIONS
   - NEVER use markdown tables in the content — they cause 400 INVALID_ARGUMENT. Use plain text with labels instead.
→ See memory: feedback_drive_oauth_vs_mcp.md

## NAMED-PERSON → FACE RULE, NON-NEGOTIABLE (added 2026-04-17)

When content names a person (politician, business owner, accused, witness, victim, worker), their face MUST appear on that slide/frame. No exceptions.

Pre-render checklist (run before exporting PNGs):
1. Scan the HTML/script for every `<strong>FirstName LastName</strong>` or named subject in body copy
2. For each named person, verify one of these exists on the same slide:
   - a `.sticker-slot` with real photo (cover / hero slide — primary subject), OR
   - a `.bio-card` with 3×4 `.bio-photo` (multi-person slide — 110×130px minimum), OR
   - a `.bio-initials` fallback card (same size, 2-letter initials) — ONLY when no licensed photo exists
3. If a name has NO face treatment → STOP. Source the photo (Wikimedia Commons CC, Agência Brasil CC BY 3.0, editorial fair-use) OR add initials card. Never render without.

CSS reference — reuse these classes across carousels (locked from EP001 Rachadinha V2):
`.bio-grid` (2-col grid) · `.bio-card` · `.bio-photo` · `.bio-initials` · `.bio-name` · `.bio-role` · `.bio-fact`
Template: `Carousel/Brazil/Quem-Decidiu-Isso/_TEMPLATE_CAROUSEL/v2_rachadinha/cover.html`

Source of directive: `~/.claude/projects/-Users-priscilahigashi/memory/project_visual_sticker_system.md`
Quote: *"every time we're talking about someone I would like an image so people know their face and this is mandatory. I want this for all of the brands. This is mine."*

## CAROUSEL FOLDER STANDARD — CROSS-NICHE, NON-NEGOTIABLE (added 2026-04-16)

Every carousel build — OPC, Brazil News, USA News, UGC, any niche — lands in the SAME Drive shape:

```
<Niche Drive>/.../<Series>/_TEMPLATE_CAROUSEL/
   v1_<slug>/          ← static PNGs + cover.html + resources/
   v1_<slug>_motion/   ← sibling (mp4 + gif + preview frame)
   v2_<slug>/          ← next version after review (auto-incremented)
   v2_<slug>_motion/
```

LOCKED ANCHORS (do NOT invent new parents):
- OPC Tip of the Week → `_TEMPLATE_CAROUSEL` ID `1PWrZfuOvyHUbTRlFNqYxdhtg-Zvv_bXb` (Marketing drive)
- Brazil Quem decidiu isso → `_TEMPLATE_CAROUSEL` ID `1Ts4OlXT_KxtYNziGmHUcsjHVh8Z7D1ds` (News drive)
- Brazil Verificamos → series `1IPLdQeTzGnWwN9MZKvfJSOXvSyK4xI5p` / `_TEMPLATE_CAROUSEL` `1QhILiMiIM9WrpHhIqXXrPs6JqoAdDijA` (News drive, confidence gate 0.70, approval required)
- Brazil A Conta que Ninguém Pagou → series `1gaLG4ObKuMx1qOb-8r63XqaKXmfRdtLI` / `_TEMPLATE_CAROUSEL` `1AwdqHecqyjGAOwPsjrYuO_NajMxLUkWH` (News drive)
- Brazil Arquivo Aberto → series `1lvDlx4jn0fNbdJJQx9NAKG56I05ePqR2` / `_TEMPLATE_CAROUSEL` `163TWpEIGxkPuh86eCBKMIraz83YHHEzR` (News drive)
- USA The History They Left Out → series `1ZDuaLyvFYLLGoOVQlOBRsOxa-WVGwJ5g` / `_TEMPLATE_CAROUSEL` `15GxuxNyZco9W9GL2CZXoeiArIp1l4I9d` (News drive — Remotion renders here; GitHub secret: SOVEREIGN_TEMPLATE_FOLDER)
- USA The Chain → series `15hYZoMVFA0u9vZR0SvZQ3z7SkSbqBEYf` / `_TEMPLATE_CAROUSEL` `1sDMyPHVYcOqZ3NK9ch4e48AaJ7KVvxL3` (News drive — bilingual carousel, same builder as Brazil; EP001 folder `1CjaNjewnNUKd3NvMmcVpET7IKF6Mk3_Z`)
- Brazil Verdade Pela Metade (FORMAT-024 — weekly debunk) → series `1r6NJ6uoKezptnolgeSfPOeKl2dccEjPd` / `_TEMPLATE_CAROUSEL` `1Tspx9SsfFxJjzh_ZdIC_exQBHe4-p-1K` (News drive — source: @marceloem23, never name in content)
- Any new series → create its own `_TEMPLATE_CAROUSEL` subfolder + add IDs here + Templates Registry tab.

RULES:
- `<slug>` = topic slug only — no date, no post_id prefix. `rachadinha`, `walnut-kitchen`, etc.
- Version auto-increments on re-build: existing v1 → next run writes v2. Never overwrites.
- Static + motion are SIBLINGS inside `_TEMPLATE_CAROUSEL`, never nested.
- Per-post editorial log Google Doc lives one level up (series folder), not inside version folder.
- NEVER save PNG/MP4/GIF to the local computer as the final destination. Work-dir is /tmp only, ephemeral. Drive is the source of truth.

ENFORCEMENT: `scripts/content_creator/main.py` uses `next_version_number()` + locked parent IDs. All skills (/content-chief, /design-carousel, /html-to-image) must emit into this structure. If a script writes elsewhere, fix the script — not the folder.

See: memory `project_carousel_folder_standard.md`.

## MOTION IS DEFAULT ON — NON-NEGOTIABLE (added 2026-04-17)

Every carousel build ships BOTH static PNGs AND motion (MP4 + GIF + preview frame). Motion = default ON. Off only when Priscila explicitly says "static only" for that specific post.

Applies to ALL paths — scripts, email preview, manual chat, any skill (/content-chief, /design-carousel, /html-to-image):
- Script (content_creator.yml): `main.py::process_one_topic` must render `motion/` subfolder with cover MP4/GIF + duplicated non-cover PNGs. Already wired — verify before emailing.
- Email preview: EVERY preview row shows BOTH `Static: folder` and `Motion: folder` links. Motion link deep-links into `/motion/` subfolder, not the parent.
- Manual chat: when I build a carousel directly in conversation (no script), default output = static + motion. Do not wait to be asked.
- Flow docs + skills: every content-producing skill states motion is ON by default.

Pre-ship audit before reporting any build done:
1. ✅ version folder exists with `v<N>_<slug>` naming
2. ✅ `png/` has all slides × variants
3. ✅ `motion/` has cover MP4 + GIF + preview_frame.jpg + non-cover PNGs duplicated (full sequence)
4. ✅ story Google Doc inside version folder
5. ✅ `resources/` folder inside version folder

If motion is empty → build is incomplete. Do NOT email preview. Fix motion, then ship.

Why this rule exists: Priscila (2026-04-17): *"we always create a motion one as well unless I say to you to not do it."* See memory: `feedback_both_versions_always.md` (updated 2026-04-17 with enforcement section).

## VISUAL-EVERY-OTHER-SLIDE RULE (added 2026-04-17)

Carousels must never ship with 3+ consecutive text-only slides between cover and sources. At least every-other middle slide carries a visual anchor.

- **News** (Quem Decidiu / O Que É / History Time / Ground News): face for every named person (see NAMED-PERSON → FACE RULE). If no person is named on that slide → contextual image: Congresso Nacional, STF, prefeitura, event photo, receipt crop, logo of the institution.
- **OPC** (Tip / Progress / Before-After): product shot, tool, material sample, detail photo, before-after crop, icon, diagram.
- **UGC**: body movement still, product shot, reaction sticker.

Script implication: `carousel_builder.py` prompts should emit a `visual_hint` per slide (`bio-card` / `product-photo` / `context-image` / `icon-row` / `none`). HTML builder uses the hint to render the visual layer. Never ship with `none` on >1 consecutive slide.

Source: Priscila (2026-04-17): *"hooks you don't wanna just have text and text... every other slide we think about how could we have an image here."* See memory: `feedback_visual_every_other_slide.md`.

## HTML → IMAGE (deterministic export) — DEFAULT FLOW, NEVER SUBSTITUTE

When Priscila says "turn this HTML into image", "convert to png", "export slides", "save the carousel", or approves an HTML design and asks for the final images:

1. USE THE SKILL: `/html-to-image` (~/.agents/skills/html-to-image/SKILL.md)
2. Script: `node "/Users/priscilahigashi/ClaudeWorkspace/Content Templates/_Scripts/export_slides.js" "<input.html>" "<output_dir>"`
3. Default Drive destination: **Marketing > Image Creation > html to image** — folder ID `1tE-2Ps8V8ZKQ4etyvzk47ZWyzeHAD2nk` (shared drive `0AIPzwsJD_qqzUk9PVA`)
4. If the carousel belongs to a specific series (News > Templates > Carousel, OPC Templates, Higashi), upload a COPY there too — but master set ALWAYS mirrors to `html to image`.

**Non-substitution rule — enforced:**
- NEVER dispatch OpenAI / Ideogram / Recraft / Seedream / Canva AI / Nano Banana to "convert HTML to image". Those are text-to-image AI — they hallucinate a new design, text drifts, layout drifts. They are ONLY valid when Priscila explicitly says "test tools" or "explore styles".
- Remotion is a sibling deterministic path for React-source templates. HTML-source = `/html-to-image`. Same-design guarantee.
- If the HTML structure is broken, FIX the HTML or adapt export_slides.js minimally — never regenerate the design in another tool.

Verification before reporting done: file count = `.slide` element count; every PNG ≥ 15KB; slide sizes differ (blank-slide bug check).
Full doc: `~/.agents/skills/html-to-image/SKILL.md` + memory `project_html_to_image_flow.md` + Flow Plans Tracker row `FLOW_html_to_image`.

## CONTENT FORMATS — living registry of approved post formats
File: ~/ClaudeWorkspace/_Master Plans & Docs/CONTENT_FORMATS.md
Drive: https://docs.google.com/document/d/1XqXSyJC_iHMTrmMxpM5ZR7S-WQxz19HhDJO1HomdncM/edit
Drive Doc ID: 1XqXSyJC_iHMTrmMxpM5ZR7S-WQxz19HhDJO1HomdncM

READ this file before producing any content (carousel, reel, hooks, copy).
WRITE to it whenever Priscila names a new format — immediately, same session.
Save with: niche, structure description, trigger keywords, series tracking.
/capture checks it on every video ingest and flags format matches in the Inbox row.
All content-producing skills (carousel, hooks, copy) must read this before producing anything.
NEVER produce content without checking if a format already exists for that niche.

Current approved formats:
- FORMAT-001: Split Screen + Sources Below — Brazil/USA News fact-check reels
- FORMAT-002: Carousel: Quem realmente decidiu isso? — Brazil News political breakdown

When she says "format", "same style", "like the X one", "series", "split screen" → check file first.

## PER-POST EDITORIAL LOG — every post gets a Google Doc (added 2026-04-16)
Every series episode or standalone post gets a dedicated Google Doc editorial log in the same Drive folder as its templates.

RULE — CREATE on new post: Name format `EP001 — [Title] — Editorial Log` or `[POST_ID] — Editorial Log`
RULE — APPEND on feedback: Any time Priscila gives feedback, direction, or a change request about a specific post → append a dated note immediately, same session. Format: `## NOTE — YYYY-MM-DD` with full details.
RULE — READ before touching: Before editing any carousel/post → read that post's editorial log doc first.
RULE — INBOX for research: If feedback generates a research task → ALSO add a row to `📥 Inbox` tab (Ideas & Inbox 1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU) so it shows up as pending.

Doc structure: Header (post info + Drive link) → HOW TO USE section → Notes in reverse-append order.
Example: EP001 Rachadinha — `https://docs.google.com/document/d/1SgVAxHCARMuFcd3xvAJs0fsBwGU9wS3ZdlcC6QgtHcU/edit`
Located: News drive > Brazil > Carousel > Quem-Decidiu-Isso > v1_rachadinha (static folder)

This rule applies to ALL skills that produce or edit content (carousel, reel, hooks, copy, html-to-image).

## CAPTURE — RUNNER-SIDE AUTO-DETECT, CHAT NEVER PICKS (added 2026-04-27)
Chat does NOT pick the project for `/capture`. Always pass `project=auto` to Capture Pipeline v2 unless Priscila explicitly named the niche. The runner classifies via transcript + caption + her notes:
- Tier 1: notes-keyword override (zero tokens)
- Tier 2: Claude Haiku JSON classify (`book|brazil|opc|ugc|usa|stocks|higashi`)
- Tier 3: confidence < 0.70 → `unrouted` (lands in Marketing/Captures - Unrouted, status "Not Identified" in Inspiration Library, weekly digest email Sundays 13:00 UTC)
Default fallback is NEVER `book` and NEVER `opc` — it is `unrouted`. See `scripts/routing.py::get_route()` and `scripts/capture/capture_pipeline.py::detect_project()`. Memory: `feedback_post_compaction_execute_pending.md`.

## CAPTURE — CONTENT IDEA GENERATION (added 2026-04-12)
Every /capture of a video MUST auto-produce content ideas from the TOPICS, even if the clip itself is not used.
The goal is always: extract topics → build original content inspired by them.

MINIMUM output per video capture:
- 1 carousel idea (with angle + slide structure outline)
- 1 reel idea (with hook + format)
- 2-3 additional topic breakdowns (each = one post idea, one concept per post)

RULE: Never use the captured person's clip unless Priscila explicitly says to. Topics = raw material. Posts = original.
RULE: Every topic idea should be "explain one thing, explain it simply" — not a summary of the whole video.

## 4AM AGENT — SELF-IMPROVEMENT ARCHITECTURE
The 4AM agent (scripts/4am_agent/) runs nightly on GitHub Actions — no local machine needed.
pattern_learner.py implements a 3-tier cost gate for plan self-improvement:

TIER 1 (free — Sheets API only, zero LLM):
  Read Flow Plans Tracker "All Docs" tab → compare DOC_ID + LAST UPDATED vs .github/agent_state/last_seen.json
  If nothing changed → EXIT. Done. Zero tokens.

TIER 2 (cheap — Drive API only, zero LLM):
  For changed docs only → fetch first 500 chars
  If change is trivial (date/typo) → EXIT. Still zero tokens.

TIER 3 (LLM — only when meaningful change detected):
  Send ONLY changed sections to claude-haiku (cheapest).
  Extract: what new rule should Claude follow?
  Write confirmed rules → 📋 Claude Rules tab in main spreadsheet.
  Update last_seen.json in GitHub repo.

RESULT: 90%+ of nights cost zero LLM tokens. Only pays when you actually change a plan doc.
Flow Plans Tracker ID: 1fggy918FgPfnMQ-dzGQk2zx9uhi2_-uWXMKGW4MA47k (All Docs tab = agent manifest)
State file: .github/agent_state/last_seen.json (in priihigashi/oak-park-ai-hub repo)

NOTE: SA key needs read access to docs in the tracker. Share new docs with oak-park-sheets@gen-lang-client-0364933181.iam.gserviceaccount.com for agent to read them.

## AIOX AGENT AUDIT — REQUIRED BEFORE AUTOMATION IS "DONE"
Any new automation, workflow, or script is NOT done until audited by the relevant AIOX agents:
- /AIOX-architect — system design, API routing, architecture decisions
- /AIOX-devops — GitHub Actions workflows, secrets, deployment
- /AIOX-dev — script quality, error handling, code correctness
Invoke them in order. If any agent flags an issue, fix it before marking done.
This applies to: new GitHub Actions workflows, new Python scripts, new integrations, new routing logic.
Skip only for minor edits (typo fixes, copy changes, formatting).

## CLAUDE.MD DRIVE MIRROR
A read-only mirror of ~/.claude/CLAUDE.md lives in Drive:
- Folder: Marketing > Claude Code Workspace > _Master Plans & Docs
- Doc name: CLAUDE_MD_MIRROR
- Doc ID: stored in reference_credentials.md after first creation
- The 4AM agent pushes an updated copy every night via Drive API
- Never edit from Drive. Local file is authoritative.
- Use the mirror to read CLAUDE.md from phone or other sessions.

## SCRIPT / CODE EDITING RULE — NON-NEGOTIABLE
Never rewrite a working script from scratch. Only change what is strictly necessary.
Before any edit: read the full file, list what you're changing and why.
Good things already in the script must be preserved. When in doubt — don't touch it.

## CODE FIX AUDIT — NON-NEGOTIABLE (added 2026-05-05)
Before committing ANY fix to validation, checking, or reviewer logic, run this checklist:
1. Trace BOTH execution paths — local build path (check_built_post / CONTENT_CREATOR_RUN) AND Drive/manual path (check_drive_folder / REVIEW_DRIVE_FOLDERS). Fix must fire on both, or explicitly document why one is exempt.
2. Every issue detected must also be auto-fixed (when FIX_MODE=analyze_and_fix) — not just reported. Trace the issue token all the way to auto_fix_drive_folder().
3. Every dependency (Pillow, API key, folder path, env var) must have a warning or fallback — never silently skip.
4. If the fix touches a path assumption (folder name, subfolder structure), add a fallback for legacy/edge layouts.
Skipping this checklist = the next audit will find the same gaps. Discovered 2026-05-05 after missing all 4 on carousel_reviewer.py image validation.

## SKILLS & AGENTS DIRECTORY
Full index of all available skills (/command) and agents (@name) lives in Drive Map — ALL DRIVES:
- Spreadsheet: Drive Map — ALL DRIVES (10qxtM_s22Z9HNVXsnBJa1WjTYCsraPa8O2uI0VEa1Zo)
- Tab: 🤖 Skills & Agents (sheetId=806704177)
- Columns: CATEGORY | SUB-CATEGORY | TYPE | COMMAND | DESCRIPTION | GOOD FOR
- 62 entries. Filter by CATEGORY column to find the right tool fast.
NOTE: Drive Map — ALL DRIVES is the master resource map. Tabs: Folders, Docs, Sheets, GitHub & Scripts, Local Scripts, Flows, Series, Skills & Agents. NOT Ideas & Inbox. NOT Spreadsheet Hub.

15 CATEGORIES:
- YOUR SYSTEM — /content-chief (Vera), /capture, /daily-planner, 4AM agent, Capture Pipeline
- COPY — /copy-chief, /copy-squad, /brand-voice, /email-sequence
- HORMOZI — /hormozi-hooks, /hormozi-offer, /hormozi-leads, /hormozi-closer
- BRAND — /brand-chief, /brand-palette, /brand-typography, /brand-identity
- TRAFFIC — /traffic-masters, /seo-chief, /paid-ads-chief, /social-chief
- ADVISORY — /advisory-simon-sinek, /advisory-gary-vee, /advisory-alex-hormozi
- STORYTELLING — /storytelling-story-chief, /storytelling-arc, /storytelling-hook
- DESIGN — /design-chief, /design-carousel, /design-thumbnail
- C-LEVEL — /ceo-chief, /cfo-chief, /cmo-chief, /coo-chief
- MOVEMENT — /movement-chief, /movement-rally, /movement-manifesto
- DATA — /data-chief, /data-analyst, /data-dashboard
- CYBERSECURITY — /cybersec-chief, /cybersec-audit
- AIOX DEV — /AIOX-analyst (Atlas), /AIOX-architect, /AIOX-dev (Dex), /AIOX-devops (Gage), /AIOX-qa (Quinn), /AIOX-squad-creator (Craft)
- CLAUDE CODE — Claude Code CLI features, hooks, MCP, slash commands
- UTILITY — /yolo, /help, /exit, general utilities

RULE: Before starting any task, check this tab — a specialized skill/agent may already exist for it.
RULE: 4AM agent invokes /content-chief (Vera) for Talking Head script generation — see 4AM AGENT section.
When she says "do we have a skill for X" → check this tab first before saying no.

## AI ANALYSIS — EVIDENCE-DRIVEN RULE (global, added 2026-04-29)

Applies to: ads dashboard MoM, weekly reports, keyword alerts, warnings, content insights — any surface where Claude outputs an analysis or recommendation.

**Rule:** Investigate first. Surface only concrete findings. Generic possibilities are an internal checklist — never print them.

4-step flow (mandatory):
1. List generic possibilities internally (seasonal, competitor, tracking, budget). Do NOT output this list.
2. Investigate each against available data: change_log (last 45d), keyword set comparison (spend ≥ $15), ad group shifts (> $100), live config.
3. Surface strongest concrete finding as WHY — name + date + number. 1–2 max.
4. Recommendation tied to the finding:
   - Change ≤ 14 days old → Wait. Smart Bidding stabilization window. Re-evaluate at +14d.
   - Change > 14 days + still degrading → specific named action.
   - No internal cause found → state explicitly what was checked + external fallback as last resort only.

Banned phrases: "Could be seasonal", "Maybe a competitor change", any (a)(b)(c) list of possibilities.
Full pattern + implementation: `~/.claude/projects/-Users-priscilahigashi/memory/feedback_evidence_driven_ai_insights.md`

## KNOWN REPEAT MISTAKES — read and prevent
1. Said "I may not have access" when connection was already active → CHECK connections file first
2. Listed completed tasks as "next steps" instead of doing them → do it now or explain why blocked
3. Wrote vague "Only YOU" items with no steps → always include why/where/what/steps
4. Referenced error codes (like "403") without explaining what they are → always explain in plain language
5. Changed the wrong spreadsheet ID when editing a script → read full script, list all IDs first
6. Guessed which spreadsheet a GitHub secret (e.g. GOOGLE_SHEET_ID) pointed to → STOP, ask for the value. Never guess. Document confirmed secret→ID mappings in reference_credentials.md
7. OAuth + My Drive folder ID → file silently goes to My Drive. Fix: use SHARED DRIVE folder ID + supportsAllDrives=true in the API request. The scope is fine; the folder ID is what determines destination.
8. Reported "uploaded" when file went to wrong location → NEVER confirm upload without verifying it appears in the correct shared drive path
9. Used markdown tables in GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN → 400 INVALID_ARGUMENT error → NEVER use markdown tables. Use plain text labels instead.
10. Script uploading to shared Drive folder returns 404 even with correct folder ID → ALWAYS check that the upload API URL includes `supportsAllDrives=true`. Missing this parameter causes 404 on any shared drive folder. Fix: add `&supportsAllDrives=true` to the upload URL. When writing or auditing any Drive upload script, verify this parameter is present.
11. `mcp__gdrive__search` returns `MCP error -32603: invalid_request` → server-side failure. Do NOT retry it. Immediately switch to `mcp__claude_ai_Google_Drive__search_files` (Route A). If that also fails, use OAuth Python Drive API (Route C). Never report Drive search as blocked without trying all 3 routes. See: reference_active_connections.md → "Drive Search & Access — 3 Routes"
12. Gmail MCP reported as "not loading in this IDE session" → it IS available but as a DEFERRED tool. Fix: call ToolSearch("select:mcp__claude_ai_Gmail__gmail_create_draft,mcp__claude_ai_Gmail__gmail_list_labels") FIRST, then call the tool. Always load deferred tools before calling them.
15. Gmail filter creation blocked with "missing gmail.settings.basic scope" → Composio Gmail connection was authorized without the settings scope. Labels/read/write work fine. Filter creation requires a one-time reconnect by Priscila at app.composio.dev → Connections → Gmail → Reconnect → check "Manage your email settings". This is NOT a Google Cloud API issue — the API is enabled. It is a Composio OAuth scope issue. After reconnect, filter creation works immediately.
14. Calendar MCP reported as "doesn't load in VSCode IDE extension" or "OAuth token has no Calendar scope" → BOTH WRONG. Calendar MCP tools are DEFERRED — load schema via ToolSearch first: ToolSearch("select:mcp__claude_ai_Google_Calendar__gcal_create_event"). Then call normally. Confirmed working 2026-04-12. OAuth scope is irrelevant — MCP uses its own auth, not sheets_token.json.
13. Gmail MCP used when task required SENDING email → Gmail MCP CANNOT send, only creates drafts. For actual sending: trigger GitHub Actions `send_email.yml` via `~/bin/gh workflow run send_email.yml --repo priihigashi/oak-park-ai-hub -f to=... -f subject=... -f body=...`. This uses PRI_OP_GMAIL_APP_PASSWORD (already set) and actually delivers the email. Same pattern as 4am_agent.yml. → this is a server-side failure from the gdrive MCP server, NOT a query formatting issue. Do NOT retry it. Immediately switch to `mcp__claude_ai_Google_Drive__search_files` (Route A). If that also fails, use OAuth Python Drive API (Route C). Never report Drive search as blocked without trying all 3 routes. See: reference_active_connections.md → "Drive Search & Access — 3 Routes"
16. Invented or assumed a GitHub secret name → SILENT FAILURE. Scripts receive empty string and fail with no error. MANDATORY: Before referencing, adding, renaming, or auditing ANY secret/API key in any script or workflow, run `~/bin/gh secret list --repo priihigashi/oak-park-ai-hub` FIRST. The list is the ONLY source of truth. Never write a key name from memory. See: memory/feedback_always_check_github_secrets_first.md
17. Trusted GitHub Actions `conclusion: success` as proof a pipeline worked. WRONG. Many scripts catch exceptions, print "failed", and exit 0 → run is marked ✅ even when Drive uploads, API calls, or whole rounds were skipped. **MANDATORY before reporting any workflow run as successful**: (a) `~/bin/gh run view <ID> --log` and grep for `failed|error|401|403|skipped|unauthorized|exception`, (b) check the `🚨 Pipeline Failures` tab in Ideas & Inbox filtered by RUN_ID. GitHub status ≠ actual success. Discovered 2026-04-27 — video-research.yml had been silently 401-ing on Drive uploads + skipping Rounds 2/3 since first deploy. See: memory/feedback_check_logs_for_silent_failures.md
18. GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN overwrites the ENTIRE document — it does NOT append. Writing to an existing doc without reading it first = all prior history is permanently destroyed. Happened 2026-05-01 on the Feedback Log (1zonzZNmW5wdDmtJxzozyqaBpf1i6KX068vEyhJ2qb_4). **MANDATORY 3-step rule for ANY write to an existing Google Doc**: (1) Read current content first via GOOGLEDOCS_GET_DOCUMENT. (2) If GET fails → STOP. Do NOT write. Do NOT assume the doc is empty. Create a NEW doc instead. (3) If GET succeeds → combine existing + new content, write the full combined result. This applies to: feedback logs, session logs, editorial logs, handoff docs, productivity docs, any doc that accumulates history. See: memory/feedback_googledocs_never_overwrite.md

## PIPELINE FAILURE LOG — 🚨 Pipeline Failures tab (added 2026-04-27)
Every pipeline writes silent + loud failures to ONE place: `Ideas & Inbox` → `🚨 Pipeline Failures` tab (sheetId 448272280).
Columns: TIMESTAMP_UTC | WORKFLOW | RUN_ID | STAGE | ERROR | RUN_URL | RESOLVED | NOTE
- Every workflow that catches an exception MUST also call a `log_pipeline_failure(stage, error, sheet)` helper that appends a row.
- Workflow YML MUST emit `if: failure()` SMTP alert to priscila@oakpark-construction.com via PRI_OP_GMAIL_APP_PASSWORD.
- Script MUST exit non-zero when any failure was recorded → GitHub run flips ❌ → email fires.
- Session-start: scan `🚨 Pipeline Failures` for unresolved rows (RESOLVED column blank) and report them as part of the status report.
- First implementation: `scripts/youtube_research.py` + `.github/workflows/video-research.yml` (commit d7c1bbb).
- Wire this into every other pipeline: capture_pipeline.yml, content_creator.yml, ads_pulse.yml, drive_route_file.yml, etc. — same helper, same tab.
