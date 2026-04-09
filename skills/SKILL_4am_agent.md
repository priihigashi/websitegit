# SKILL: 4AM Content Agent
**Triggers:** "run the 4AM agent now" · "run 4am agent" · "trigger content agent" · "generate today's content"

---

## What this does
Runs the full daily content automation pipeline for Oak Park Construction:

1. Reads **Scraping Targets** tab → gets Instagram/TikTok accounts, hashtags, keywords per niche
2. Scrapes via **Apify** → filters to 10,000+ views only (rejects anything below)
3. **Claude picks 2 Talking Head topics** Mike can film without being on a specific job site
4. Writes a **60-second script** for each topic
5. Finds **3–5 free Pexels B-roll clips** per script
6. Updates **Clip Collections** tab if any topic is still collecting
7. Appends **2 new rows** to Content Queue (Status: Pending)
8. Sends **ntfy.sh push notification** with summary
9. Logs run to **Runs Log** tab (Date, Status, Topics, Clips, Errors, Lessons Learned)
10. Runs **pattern_learner** → if recurring errors found, auto-creates skill files or Calendar tasks

---

## To trigger manually (terminal)
```bash
cd /tmp && git clone https://github.com/priihigashi/oak-park-ai-hub.git && cd oak-park-ai-hub/scripts/4am_agent
pip install anthropic google-auth google-auth-httplib2 google-api-python-client requests pytz
export APIFY_API_KEY=... PEXELS_API_KEY=... ANTHROPIC_API_KEY=... GOOGLE_SA_KEY=... NTFY_TOPIC=...
python main.py
```

## To trigger via GitHub Actions (no local setup needed)
```bash
~/bin/gh workflow run 4am_agent.yml --repo priihigashi/oak-park-ai-hub
```

## To watch it run live
```bash
~/bin/gh run list --repo priihigashi/oak-park-ai-hub --workflow=4am_agent.yml --limit 5
~/bin/gh run watch --repo priihigashi/oak-park-ai-hub
```

---

## Schedule
Runs automatically at **4 AM ET every day**
- EDT (Mar–Nov): cron `0 8 * * *` (UTC-4)
- EST (Nov–Mar): change to `0 9 * * *` (UTC-5)

---

## Spreadsheet
[Open Ideas & Inbox spreadsheet](https://docs.google.com/spreadsheets/d/1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU/edit)

| Tab | Purpose |
|---|---|
| Scraping Targets | Add accounts/hashtags/keywords to scrape |
| Clip Collections | Tracks clip gathering progress |
| 📋 Content Queue | New rows land here (Status: Pending) |
| 📊 Runs Log | Every run logged with lessons learned |

---

## GitHub Secrets required
| Secret | Status |
|---|---|
| APIFY_API_KEY | ✅ Set |
| PEXELS_API_KEY | ✅ Set |
| ANTHROPIC_API_KEY | ✅ Set |
| GOOGLE_SA_KEY | ✅ Set |
| NTFY_TOPIC | ⚠️ Must be set — see setup below |

---

## NTFY_TOPIC setup (one-time, 5 minutes)
This is how you get push notifications on your phone when content is ready.

1. Install the **ntfy** app on your phone (free, iOS or Android — search "ntfy")
2. Open the app → tap **+** → Subscribe to topic → type: `oak-park-content-4am`
3. Add the secret to GitHub:
   ```bash
   ~/bin/gh secret set NTFY_TOPIC --repo priihigashi/oak-park-ai-hub
   # When prompted, type: oak-park-content-4am
   ```
4. That's it. Next time the agent runs, your phone gets a notification.

---

## Pattern Learner (built-in)
After every run, the agent reads the last 14 Runs Log entries and asks Claude:
- "Are there recurring errors or lessons?"
- If yes and fixable → auto-creates a skill `.md` file in `skills/` on GitHub
- If yes but needs human → creates a Google Calendar task with exact steps
- Sends a push notification either way

This means the agent gets smarter over time without you doing anything.
