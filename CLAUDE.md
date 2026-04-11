# CLAUDE.md — Oak Park AI Hub
# Instructions for Claude Code and Claude chat agents working in this repo.

---

## WHAT THIS REPO IS

Automation hub for Oak Park Construction social media and content pipeline.
Also the website hub: /websites/ contains OPC site + /websites/higashi-imoveis/ (Brazil RE).
Owner: priscila@oakpark-construction.com
Instagram: @oakparkconstruction
License: CBC1263425 | South Florida | 954-258-6769

---

## REPOS — ONLY ONE IS LIVE

✅ ACTIVE: **priihigashi/oak-park-ai-hub** (this repo, created Mar 26 2026)
   - All 15 GitHub Actions, all scripts, all websites live here
   - Python primary, /websites/ for HTML sites
   - This is the source of truth

❌ ABANDONED: **priihigashi/Oak-park-projects-** (created Mar 24 2026)
   - Stale one-shot commit from before the hub was structured
   - NOT referenced by any workflow, script, or config in this repo
   - Should be archived via github.com web UI (Settings → Archive this repository)
   - DO NOT push to it. DO NOT read from it. DO NOT clone it.

If you (Claude) see anything suggesting work should happen in Oak-park-projects-,
STOP and flag it. That repo is dead.

---

## GITHUB ACTIONS — ALL WORKFLOWS

### 1. build-carousels.yml — Carousel Builder
Trigger: Cron 6PM ET daily OR workflow_dispatch (manual)
Script: scripts/build_carousel_cloud.py
What it does: Reads Content Queue spreadsheet → builds slide images → saves to Drive Reels & TikTok folder

TRIGGER LOGIC:
- workflow_dispatch with input source=chat → grabs LATEST row regardless of status (chat-triggered build)
- workflow_dispatch with no source → only processes rows with status=Approved
- Cron schedule → only processes rows with status=Approved

⚠️ CRITICAL PIPELINE RULE:
This workflow is for the AUTOMATED pipeline ONLY.
NEVER trigger this for carousels created in Claude chat sessions.
For chat-created carousels → build HTML file in chat → user screenshots → posts.
The script generates its OWN layout via Gemini AI. It does NOT use slide-by-slide copy written in chat.

PHOTO SOURCING (auto):
- If col D (photo) is empty or TBD → script searches Pexels API (PEXELS_API_KEY secret)
- Query built from service type + hook keywords
- Photo saved to Drive → Reels & TikTok subfolder → col D and E updated automatically
- Photo must match copy TOPIC and EMOTIONAL TONE — not just the service category

### 2. 4am_agent.yml — Daily Content Agent
Trigger: Cron 9:05 AM ET daily
Script: scripts/4am_agent/ (daily_content_processor.py + inspiration_scraper_cloud.py)
What it does: Scrapes inspiration → generates content ideas via Gemini → drops into Content Queue spreadsheet (status=Idea)

This is why the Content Queue has 80+ rows — the agent fills it every morning automatically.
The user does NOT need to add ideas manually unless they want to.

### 3. Other workflows
- add-gsc-headers.yml — Google Search Console sync
- Additional workflows exist for blog generation and content scheduling

---

## SPREADSHEETS

Main spreadsheet (Ideas & Inbox): 1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU
Tabs: 📥 Inbox, 📱 Content Inspo, ✅ Done/Archive, 📸 Photo Catalog, 📋 Content Queue,
      📥 Inspiration Library, 📊 Analytics, 🎯 Accounts Tracking, Clip Collections, Scraping Targets

Content Control spreadsheet: 1C1CAZ8lSgeVLSSCYIg-D9XPJcSLHyIOh1okKtvhZZQg

Status values in Content Queue (col J):
  Idea → Approved → Built → Review → Ready to Post → Posted

---

## TWO CAROUSEL PIPELINES — NEVER MIX THEM

### CHAT PIPELINE (Claude.ai chat session)
1. Discuss topic → 3 Hormozi hook options → user picks one
2. Show 3 Slide 1 theme previews (Dark / Light / Warm) as HTML in chat
3. User picks theme
4. Build full 5-slide HTML with exact copy + chosen theme → present in chat immediately
5. Output path: /mnt/user-data/outputs/carousel_[topic].html
6. User opens in browser → screenshots each slide → posts to Instagram
7. Log to Content Queue for records (status=Built). DO NOT trigger GitHub build.

### AUTOMATED PIPELINE (GitHub Actions)
1. 4AM agent → Content Queue (status=Idea)
2. User/Claude reviews → changes status to Approved
3. build-carousels.yml runs (cron 6PM or workflow_dispatch source=chat)
4. Script auto-sources photo via Pexels API if col D is empty
5. Slides built → Drive → Reels & TikTok folder
6. Status updated: Built → Review → Ready to Post

---

## CAROUSEL SLIDE SPEC

Format: 1080 x 1440px (3:4 ratio) — preview in chat at 360x480
Background: Black #000000
Accent: Yellow #CBCC10
Headline font: Anton (Google Fonts)
Body font: Roboto (Google Fonts)
Slides: 5 — Hook / Problem / Contrast / Tip / CTA (Hormozi framework)
HTML output path: /mnt/user-data/outputs/

---

## PHOTO SOURCING RULES

Tips/educational carousels (no project):
→ Pexels API auto-sourced (PEXELS_API_KEY in GitHub secrets)
→ Query from hook + service keywords, portrait orientation

Project carousels:
→ Google Drive → Oak Park Construction shared album → Photos and Videos
→ Match photo to project name and service type

⚠️ Photo must match copy TOPIC and EMOTIONAL TONE — not just service category.
Wrong example: using a kitchen photo for a "contractor warning" post = looks like a kitchen post.

---

## BRAND

Company: Oak Park Construction
License: CBC1263425
Email: priscila@oakpark-construction.com
Phone: 954-258-6769
Instagram: @oakparkconstruction
Website: www.oakpark-construction.com
Service area: South Florida (Pompano Beach focus)

Colors: Black #000000, Yellow #CBCC10
Fonts: Anton (headlines), Roboto (body)
Tone: Direct, anti-hype, Hormozi-style. Short sentences. Specific numbers.

---

## NANO BANANA (Gemini Image Generation)

Tool: GEMINI_GENERATE_IMAGE via COMPOSIO
Model: gemini-3-pro-image-preview
Aspect ratio for Instagram carousels: 3:4
Requires paid Gemini API plan (free tier quota = 0 for image generation)
When working: generates actual slide images → can post directly to Instagram via COMPOSIO Instagram tools
Status: Needs paid GEMINI_API_KEY with image generation quota enabled

---

## LESSONS LEARNED (April 2026)

See Drive: CAROUSEL_FLOW_LESSONS — April 2026

Key failures to never repeat:
1. GitHub build script generates its OWN content — does not use copy written in chat
2. Photo category ≠ copy topic. Kitchen photo on contractor warning post = wrong post identity
3. Always open and verify build output before presenting as done
4. Never route a chat carousel job through GitHub build
5. After any GitHub build, verify the output slides match the copy before sending link to user
