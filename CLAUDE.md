# Claude Global Rules — Oak Park Construction / Priscila
# Every Claude session reads this first. These rules are non-negotiable.

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
Instagram: Composio MCP ✅ (only option — no Google Cloud equivalent)
Canva: mcp__claude_ai_Canva__ tools ✅ (DEFERRED — load via ToolSearch)
Full details: ~/.claude/projects/-Users-priscilahigashi/memory/reference_active_connections.md

## BEFORE TOUCHING ANY SCRIPT
0. Read NONNEGOTIABLES.md (repo root) FIRST — verify your change does not break a locked rule
1. Read the full script first
2. Extract and list every spreadsheet ID, folder ID, file path, and env var referenced
3. Show what you are about to change and why BEFORE making any change
4. Never assume a variable value — verify it from the source file
→ See memory: feedback_script_investigation_rule.md
→ See NONNEGOTIABLES.md for locked features that must never be removed

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
  OPC     → 1lyWGwQiUPAVoMzb8vfQ0fBw72M1A2UfR (Marketing/Content/Captures, confirmed 2026-04-20)
  Brazil  → 1DZWbS4bF4XF_OjJSnD02WD2N83ljXwHd (News/Brazil/Captures)
  USA     → 1ZzrEmj3Smt0chr8CxiCOyroFCRzE-zU1 (News/USA/Captures)
  UGC     → 1b5fCmWn6cUkZSjhaZKGFmaKDc4MafY3U (UGC/Captures)
  Stocks  → 17oazrbMM1lBeFAGNCaFp8sjnAMWbVdSI (Stocks/Captures)
  Higashi → 1by4guSe46XK0DwIJwmNUEtbzmvQFOXOv (Higashi/Captures)
Carousel parent folders (per niche — routing.py: carousel_folder_id):
  OPC     → 1j_wiygaY0ltLkOp9-etiDDsA4R4n5ecm (Marketing/Content/Series)   ← series _TEMPLATE_CAROUSEL folders nest here ✅
  Brazil  → 1gDOjtW_X-_jWtu94pffbDaUsw6VGCKuA (News/Brazil/Carousel)       ← series live at News/Brazil/Content/Series/* (migration pending)
  USA     → 1lRfZE5XC_gL57pUiiWu0Lhar9wfyCtFw (News/USA/Carousel)          ← series live at News/USA/Content/Series/* (migration pending)
Reels_Shorts parent folders (per niche — routing.py: reels_folder_id):
  OPC     → 1jW3WUQEPpfJNgje-4YGyFT4inKgzWrt7 (Marketing/Content/Reels_Shorts)
  Brazil  → 1IY4TJyv9Dk1qJPdhskyn4flj1g1jp0Kl (News/Brazil/Reels_Shorts)
  USA     → 1EN2HhPzmUnwjXhXpaaf1hO52REAo7wB0 (News/USA/Reels_Shorts)
NOTE: content_creator/main.py hardcodes series-level _TEMPLATE_CAROUSEL IDs and does NOT yet read carousel_folder_id from routing.py. OPC ✅ aligns. Brazil/USA series live at <niche>/Content/Series/ not under <niche>/Carousel/ — pending Priscila decision to migrate or update routing.
Content Creation (Drive): 1um7y2Yt8zi9KGxev6kfFJYgrkMYwrCNh — production workspace (Art/Caption/Reel + Claude brief). Created alongside Content Hub on every capture.

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

BYPASS LEVEL GUIDE (use when she says S or doesn't answer):

SAFE TO BYPASS — recommend Y:
- Building/editing HTML, carousels, websites, scripts
- Reading Drive / Sheets / Calendar / GitHub
- Writing to Drive docs, sheets, spreadsheets
- Running GitHub Actions (read-only or non-destructive)
- Content creation, planning tasks

DO NOT BYPASS — recommend N:
- Sending emails via Gmail
- Deleting Drive files or folders permanently
- Posting to Instagram or any social platform
- GitHub force push or any destructive git op
- Any financial, legal, or client-facing document
- Any task touching McFolling Properties client data

IF she says Y: proceed in current session — note that Claude Code's MCP tools still show prompts within the IDE (that's the IDE, not Claude asking). For full CLI bypass, restart with: claude --dangerously-skip-permissions
IF she says N: ask before each action that modifies external state (Drive, GitHub, Gmail, Instagram)
IF she says S: check today's calendar tasks, apply guide above, state the recommended level + one-line reason

## SESSION START — DO THESE IN ORDER
1. Check Google Calendar for today's tasks — report: X tasks pending, last session was [date]
2. Check Inspiration Library tab (Ideas & Inbox) for pending /capture items
3. Check Chat Logs folder in Drive (ID: 1qitnbz5_8tfZI2rnTogV1zLLLLOwFVCw) for yesterday's log
4. Report status: "X tasks pending, Y items need /capture, last session was [date]"
5. Ask bypass mode question (see SESSION PERMISSIONS above)
Key Drive docs: Content_Creation_Master_Plan.docx (_Master Plans & Docs), SKILL_daily_planner.md (Agents & Skills), AI_Content_Ideas_April2026.docx (Content-Creation), Ads_Strategy.docx (root of ClaudeWorkspace)
Key spreadsheets: Ideas & Inbox 1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU (tabs: Inspiration Library, Content Queue, Scraping Targets, Clip Collections) | Content Control 1C1CAZ8lSgeVLSSCYIg-D9XPJcSLHyIOh1okKtvhZZQg
Flow Plans Tracker (all master/flow docs indexed): 1fggy918FgPfnMQ-dzGQk2zx9uhi2_-uWXMKGW4MA47k

## WHEN SHE DROPS A URL
Save to Inspiration Library tab (Ideas & Inbox) immediately. Create calendar task with full URL, /capture instructions, purpose, Drive links.

## CALENDAR TASKS must always include: source URLs, numbered action steps, tools, Drive links

## CREATING CALENDAR EVENTS — try in order, never give up:
ROUTE A: mcp__claude_ai_Google_Calendar__ tools (preferred)
ROUTE B: Composio MCP — GOOGLECALENDAR_CREATE_EVENT action
ROUTE C: Python OAuth — build('calendar','v3',credentials=creds).events().insert(calendarId='primary', body=event).execute()
         NOTE: sheets_token.json does NOT have calendar scope. Need separate token or re-auth with calendar scope.
         If Route C fails with 403 insufficient scope → skip and use Route A or B only.
NEVER tell Priscila to add the calendar event herself unless all routes are tried and all fail.

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

## CONTEXT FULL / NEW CHAT HANDOFF RULE (added 2026-04-13)
When the context window is running out OR before starting a new chat on an ongoing task:
1. Create a doc named HANDOFF_YYYY-MM-DD in Drive folder: Productivity & Routine (ID: 1b8Cfc8lJhu5unDaxDQIdo4xdN6X7n1nS)
2. Write full content via GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN — include:
   - What was done (files, commits, runs, IDs)
   - What is pending (priority ordered)
   - Errors that happened + fixes applied
   - All key IDs (Drive folders, docs, spreadsheets)
   - Which skill to invoke in next chat
3. Write standard chat log to Chat Logs folder (ID: 1qitnbz5_8tfZI2rnTogV1zLLLLOwFVCw)
4. Never leave a new chat cold — always write the handoff BEFORE context is fully exhausted
TRIGGER: If Priscila says "start new chat", "context is full", "I'm gonna start a new chat", or session summary is generated automatically — execute this immediately.

## SESSION EXIT LOG
When she says "exit" or "closing" or "done for today":

Do these THREE things in order:

STEP 1 — CHAT LOG
Log must include: date + session duration estimate, what was discussed, what was actually implemented (files/cells/runs), what was promised but NOT done (carry-forward), Drive links to anything touched.

STEP 2 — PRODUCTIVITY & ROUTINE DOC (ID: 1wVBuNOuOufT8WP4KCrrlVbKWRmQZjKvqmia1soUEBZE)
Read the current doc. Then:
- Mark any tasks completed this session as DONE (or remove them)
- Add any NEW ongoing tasks that came up this session
- This doc = source of truth for what's actively in-progress across all projects

STEP 3 — HANDOFF (if context is near limit or session was long)
Write a full handoff summary to the same doc above so the next chat can pick up immediately.
See: CONTEXT FULL / NEW CHAT HANDOFF RULE section for full format.

ROUTE A — try first (Composio):
1. Create empty Google Doc via Drive MCP in Chat Logs folder (Drive ID: 1qitnbz5_8tfZI2rnTogV1zLLLLOwFVCw), name: LOG_YYYY-MM-DD_HHMM
2. Write content via GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN (Composio — if googledocs not active, call COMPOSIO_MANAGE_CONNECTIONS toolkit:googledocs → wait → retry)

ROUTE B — fallback if Composio fails (OAuth + Docs API):
1. Create empty Google Doc via Drive MCP (same folder, same name)
2. Write content via Python google-auth:
   from googleapiclient.discovery import build
   creds = Credentials.from_authorized_user_file('/Users/priscilahigashi/ClaudeWorkspace/Credentials/sheets_token.json')
   docs = build('docs', 'v1', credentials=creds)
   docs.documents().batchUpdate(documentId=DOC_ID, body={'requests': [{'insertText': {'location': {'index': 1}, 'text': CONTENT}}]}).execute()
   Google Docs API is ENABLED on project 334842470868 as of 2026-04-11 — this works.

ROUTE C — last resort if both fail:
   Upload plain text file via Drive OAuth (drive.files().update with text/plain mimetype).
   Still saves everything, just no formatting.

Never give up and tell Priscila to add it manually. Always try all 3 routes before saying blocked.
Keep only last 7 days — delete older logs.
Confirm: "Session log saved to Chat Logs/LOG_[date]" with Drive link.

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

ROUTING BY PROJECT — always check which drive before creating anything:
- Higashi / Hig Negócios / mom's site / Alexandra → Shared Drive "Higashi Imobiliária - Claude" (ID: 0AN7aea2IZzE0Uk9PVA) → Claude Flow → Website — Hig Negócios Imobiliários (ID: 1CKWTojSg2uQmXjNnKlAaSBCTfxtSQBvH)
- OPC / Oak Park / McFolling / content / marketing → Shared Drive "Marketing" (ID: 0AIPzwsJD_qqzUk9PVA) → Claude Code Workspace
- NEVER mix these. Higashi files must NEVER land in Marketing. Marketing files must NEVER land in Higashi.

Default for non-Higashi tasks: Shared Drive "Marketing" → "Claude Code Workspace" → [project folder]
NEVER upload to My Drive. If Priscila doesn't specify otherwise, always use the correct project drive above.

Tool rules for Drive:
- CREATE folders → `mcp__claude_ai_Google_Drive__create_file` (mimeType: folder) ✅
- WRITE content to a doc → `GOOGLEDOCS_UPDATE_DOCUMENT_MARKDOWN` via Composio ✅
- UPLOAD files (video/pdf/etc) → OAuth resumable upload with supportsAllDrives=true + SHARED DRIVE folder ID ✅
- DO NOT use `mcp__claude_ai_Google_Drive__create_file` with content — it ALWAYS fails silently (creates empty file)
- OAuth + My Drive folder ID → file goes to My Drive (wrong). OAuth + SHARED DRIVE folder ID + supportsAllDrives=true → file goes to correct shared drive folder ✅
- Use resumable upload for files >5MB: POST to /upload/drive/v3/files?uploadType=resumable&supportsAllDrives=true

## EMAIL SENDING — 3 ROUTES (added 2026-04-12 — prevents "Gmail blocked" from stopping work)
Gmail MCP = DRAFT only (no send tool exists). For actual sending, use GitHub Actions.
- ROUTE A (draft): Load ToolSearch first → mcp__claude_ai_Gmail__gmail_create_draft → Priscila clicks Send
- ROUTE B (SEND — preferred): ~/bin/gh workflow run send_email.yml --repo priihigashi/oak-park-ai-hub -f to="..." -f subject="..." -f body="..."
  Uses PRI_OP_GMAIL_APP_PASSWORD secret (already set). Actually delivers. Same as 4am_agent.yml.
- ROUTE C (SEND — local): smtplib.SMTP_SSL smtp.gmail.com:465 with app password from .env (not set locally yet)
DEFERRED TOOL RULE: Gmail MCP tools need ToolSearch schema load before calling — they are deferred, not absent.

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

## SKILLS & AGENTS DIRECTORY
Full index of all available skills (/command) and agents (@name) lives in the main spreadsheet:
- Spreadsheet: Ideas & Inbox (1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU)
- Tab: 🤖 Skills & Agents
- Columns: CATEGORY | SUB-CATEGORY | TYPE | COMMAND | DESCRIPTION | BEST FOR
- ~150 entries. Filter by CATEGORY column to find the right tool fast.

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


## MOTION CHAIN — RENDERERS + SOURCES (added 2026-04-20)

Every motion render (carousel cover, reel, any animated slide) goes through TWO cascading chains. Ken Burns is always the floor so rendering never fails silently.

### Renderer cascade (order matters)
1. **Remotion** — `scripts/remotion/src/CarouselMotion.tsx`, composition id `CarouselMotion`, 1080×1350 @ 30fps. Used when `cover_renderer_pref == "remotion"` or the series is template-driven. Writes MP4 + GIF + preview frame.
2. **Playwright** — `scripts/remotion/record_motion.js` or equivalent HTML recorder. Used for slides whose motion lives in the HTML itself.
3. **ffmpeg Ken Burns zoompan** — always-succeeds floor. Animates the poster PNG. Never skipped.

Never substitute AI-video tools (Kling / Runway / Pika) unless Priscila explicitly says so per post.

### Video source cascade — 8 tiers
Module: `scripts/content_creator/motion_sources.py` → `fetch_clip_with_fallback(slide_cfg, work_dir, filename, visual_hint)`.

Order: YouTube (Apify) → Instagram (Apify) → Pexels → Pixabay → Archive.org → Wikimedia Commons → stock scrapers → Ken Burns poster animation.

Rules:
- Every tier gets its OWN query string (`youtube_query`, `instagram_query`, `pexels_query`, `pixabay_query`, `archive_query`, `wikimedia_query`). Haiku emits them in `clip_suggestions[*]`.
- Every fetched clip writes a `<clip>.source.txt` sidecar with tier, url, license, attribution, query, fetched_at.
- Stock tiers (Pexels/Pixabay/Archive/Wikimedia) are gated — they skip automatically when `visual_hint == "bio-card"` so faces never come from generic stock.
- Clips live in `<work_dir>/resources/clips/` alongside `png/` and `motion/`.
- Silent-fail per tier with one-line log. Never raise until every tier exhausted.

### Env / secret requirements
- `APIFY_API_KEY` — YouTube + Instagram scraping (GitHub secret ✅)
- `PEXELS_API_KEY` — stock video (GitHub secret ✅)
- `PIXABAY_API_KEY` — stock video (optional; skips tier if missing)
- `CLAUDE_KEY_4_CONTENT` — Haiku content generation (separate billing from blog)

### Per-slide JSON schema (extension in `clip_suggestions`)
```
{
  "slide": 3, "duration_hint": "5-8 seconds",
  "youtube_query": "proper names OK — speeches, press",
  "instagram_query": "lowercase hashtag-friendly phrasing",
  "pexels_query": "place/event/institution — NO proper names",
  "pixabay_query": "different wording than pexels_query",
  "archive_query": "archival / public-domain phrasing",
  "wikimedia_query": "CC-licensed institutional/historical",
  "motion_prompt": "5s direction: camera move + mood + framing",
  "motion_renderer": "remotion|playwright|kenburns",
  "visual_hint": "bio-card|context-image|place|event|product-photo|none"
}
```

### When to read this
- Any edit to `scripts/content_creator/carousel_builder.py`, `main.py`, `motion_sources.py`, or `scripts/remotion/src/`.
- Any new niche pipeline. Motion chain rules apply cross-niche: Brazil News, USA News, OPC, UGC, Higashi.
- Any new Haiku prompt — it MUST populate all 6 tier queries + motion_prompt + motion_renderer.

Full research + schema + testing matrix: `scripts/content_creator/MOTION_SOURCES_RESEARCH.md`.

---

## CAPTURE PIPELINE — PROJECT ROUTING (updated 2026-04-17)

When triggering capture_pipeline.yml, always use the correct `project` input:

- `brazil` → Brazil civic content (default for Brazilian reels)
- `usa` → USA news/content  
- `book` → RECEIPTS fact-check book

RULE: Never default to `book` for Brazil reels. Always ask "brazil, usa, or book?" if not specified before triggering.

---

## 2026-04-18 — CAPTURE PIPELINE AUDIT & FIXES

### What We Found (Problems)

**Dual Pipeline Confusion**
- There were two email types: "News capture done" and "OPC capture done" — appeared to be different scripts but is actually ONE script (`capture_pipeline.py`) with two project modes
- The naming was inconsistent: queue used `brazil`/`usa`, pipeline used legacy `sovereign`/`content` aliases — caused confusion and would crash if `brazil` was typed in the manual trigger

**Capture Queue Failures (Root Causes)**
- `instaloader` was never installed in the GitHub Actions runner — the fallback that bypasses Instagram's shared_data block was silently skipped every time
- Apify actor_id format was wrong: `apify/instagram-scraper` should be `apify~instagram-scraper` — Apify REST API returns 404 with slash format
- Manual captures never marked their queue row as done → 6AM queue processor would retry the same URL, hit Instagram rate limits, and fail
- 10 rows stuck with ⚠️ failures as a result

**YouTube captures**: GitHub runner IPs are blocked by YouTube — not fixable in pipeline code, needs residential proxy or non-cloud runner

### What We Fixed

- **Renamed**: SOVEREIGN → "brazil"/"usa" canonical projects (news alias = brazil), content → "opc" project (legacy names still work via alias)
- **Email subjects**: "News capture done — NWS-..." and "OPC capture done — niche | ..."
- **Installed instaloader** in capture_queue.yml pip install step
- **Fixed Apify actor_id**: `apify/instagram-scraper` → `apify~instagram-scraper`
- **Added `_mark_queue_processed(url)`**: called at end of all 3 run_*() functions — marks queue row TRUE so 6AM run skips it
- **Added `retry_failed` input**: triggers retry of all ⚠️ rows in one run
- **Added `bulk_urls` input**: paste newline-separated URLs, adds + processes immediately

### Features Ported Between Pipelines

**OPC → News (things News was missing):**
- Bilingual content brief (EN + PT-BR doc in Drive folder)
- Inspiration Library row written on every capture
- Topic cluster scraper triggered on capture

**News → OPC (things OPC was missing):**
- SRT captions generated for non-YouTube captures

### Side-by-Side Pipeline Comparison

| Feature | News Pipeline (run_news) | OPC Pipeline (run_opc) |
|---|---|---|
| Story ID prefix | NWS- | CNT- |
| Drive destination | routing.py capture_folder_id (Brazil/USA Captures) | routing.py capture_folder_id (OPC Content Hub) |
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
- Run retry: Actions → Capture Queue Processor → retry_failed=true → max_per_run=10
- YouTube failures still need residential proxy / non-cloud runner solution
- Monitor if instaloader fix holds for the 10 stuck rows
