#!/usr/bin/env python3
"""
website_research.py — Website Reference Research Agent
Scrapes reference URLs, analyzes design/UX patterns with Claude,
writes findings to Google Sheet (Website Research tab) + Google Doc.

Usage:
  python3 website_research.py --opc "url1,url2,url3" --brazil "url1,url2,url3"
  python3 website_research.py --sheet  # read URLs from sheet instead

GitHub Action: website-research.yml triggers this script.
"""

import os
import sys
import json
import argparse
import requests
from datetime import datetime

try:
    from bs4 import BeautifulSoup
except ImportError:
    os.system("pip install beautifulsoup4 -q")
    from bs4 import BeautifulSoup

try:
    import anthropic
except ImportError:
    os.system("pip install anthropic -q")
    import anthropic

try:
    import gspread
    from google.oauth2 import service_account
except ImportError:
    os.system("pip install gspread google-auth -q")
    import gspread
    from google.oauth2 import service_account

# ── CONFIG ──────────────────────────────────────────────────────────────────
CLAUDE_KEY_4_CONTENT = os.environ.get("CLAUDE_KEY_4_CONTENT")
GOOGLE_SA_KEY     = os.environ.get("GOOGLE_SA_KEY")          # JSON string
SHEET_ID          = "1IrFrCNGVIF7cvAr9cIuAXvCtUR_-eQN1mdCpHXpfbcU"
MASTER_PLAN_DOC   = "1uxHmQtYfqel6X9MgFoXF-L_Y3rBhlenhG9sZo9ifuGU"
RESEARCH_TAB      = "Website Research"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ── GOOGLE SHEETS ────────────────────────────────────────────────────────────
def get_sheet():
    if GOOGLE_SA_KEY:
        sa_info = json.loads(GOOGLE_SA_KEY)
    else:
        # local fallback — use file
        sa_path = os.path.expanduser(
            "~/ClaudeWorkspace/Credentials/service_account_blog.json"
        )
        with open(sa_path) as f:
            sa_info = json.load(f)

    creds = service_account.Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)


def ensure_research_tab(spreadsheet):
    """Create Website Research tab if it doesn't exist."""
    try:
        ws = spreadsheet.worksheet(RESEARCH_TAB)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=RESEARCH_TAB, rows=500, cols=12)
        ws.append_row([
            "Date", "Site", "URL", "Style/Vibe", "Colors",
            "Key Sections", "Animations/Interactions", "CTAs",
            "Nav Structure", "Mobile Feel", "What to Steal", "Notes"
        ])
        print(f"Created tab: {RESEARCH_TAB}")
    return ws


# ── SCRAPING ────────────────────────────────────────────────────────────────
def scrape_url(url):
    """Fetch URL and extract text content + meta."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove scripts/styles
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title else ""
        meta_desc = ""
        meta = soup.find("meta", attrs={"name": "description"})
        if meta:
            meta_desc = meta.get("content", "")

        # Get visible text (first 3000 chars to stay within Claude context)
        text = " ".join(soup.get_text(separator=" ").split())[:3000]

        # Get nav links
        nav_links = [a.get_text(strip=True) for a in soup.select("nav a")][:15]

        # Get headings
        headings = [h.get_text(strip=True) for h in soup.select("h1,h2,h3")][:10]

        # Get CTAs (buttons)
        ctas = [b.get_text(strip=True) for b in soup.select("button, .btn, .cta, a.button")][:10]

        # Get color hints from inline styles (basic)
        style_tags = " ".join([s.string or "" for s in soup.select("style")])[:1000]

        return {
            "url": url,
            "title": title,
            "meta_desc": meta_desc,
            "text_sample": text,
            "nav_links": nav_links,
            "headings": headings,
            "ctas": ctas,
            "style_sample": style_tags,
            "status": "ok"
        }
    except Exception as e:
        return {"url": url, "status": f"error: {e}", "text_sample": ""}


# ── CLAUDE ANALYSIS ─────────────────────────────────────────────────────────
def analyze_with_claude(scraped, site_type):
    """Use Claude to extract design/UX insights from scraped content."""
    client = anthropic.Anthropic(api_key=CLAUDE_KEY_4_CONTENT)

    prompt = f"""You are analyzing a website for design/UX inspiration for a {site_type} website.

Site: {scraped['url']}
Title: {scraped.get('title', '')}
Meta: {scraped.get('meta_desc', '')}
Nav items: {scraped.get('nav_links', [])}
Headings: {scraped.get('headings', [])}
CTAs found: {scraped.get('ctas', [])}
Page text sample: {scraped.get('text_sample', '')[:1500]}
Style sample: {scraped.get('style_sample', '')[:500]}

Analyze this website and return a JSON object with these exact keys:
{{
  "style_vibe": "2-3 words describing the overall feel (e.g. minimal luxury, bold modern, warm professional)",
  "colors": "describe the color palette — dominant colors, accents, background",
  "key_sections": "list the main page sections you can infer (e.g. hero, services, portfolio, testimonials, contact)",
  "animations": "describe any animations or interactions you can infer from the page structure and style",
  "ctas": "main call-to-action text and placement",
  "nav_structure": "describe the navigation structure",
  "mobile_feel": "guess whether this feels mobile-first or desktop-first, and why",
  "what_to_steal": "top 2-3 specific things worth borrowing for our website",
  "notes": "anything else notable — typography, imagery style, tone of voice"
}}

Return ONLY the JSON. No explanation before or after."""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        text = msg.content[0].text.strip()
        # Strip markdown code blocks if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        return {
            "style_vibe": "parse error",
            "colors": "",
            "key_sections": "",
            "animations": "",
            "ctas": "",
            "nav_structure": "",
            "mobile_feel": "",
            "what_to_steal": "",
            "notes": str(e)
        }


# ── MAIN ────────────────────────────────────────────────────────────────────
def process_urls(opc_urls, brazil_urls):
    spreadsheet = get_sheet()
    ws = ensure_research_tab(spreadsheet)
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    all_results = []

    for site_type, urls in [("Oak Park Construction", opc_urls), ("Brazil Real Estate", brazil_urls)]:
        for url in urls:
            if not url.strip():
                continue
            print(f"\nResearching: {url}")
            scraped = scrape_url(url.strip())

            if scraped["status"] != "ok":
                print(f"  Skipping — {scraped['status']}")
                ws.append_row([today, site_type, url, "SCRAPE FAILED", "", "", "", "", "", "", "", scraped["status"]])
                continue

            print(f"  Scraped OK — analyzing with Claude...")
            analysis = analyze_with_claude(scraped, site_type)

            row = [
                today,
                site_type,
                url,
                analysis.get("style_vibe", ""),
                analysis.get("colors", ""),
                analysis.get("key_sections", ""),
                analysis.get("animations", ""),
                analysis.get("ctas", ""),
                analysis.get("nav_structure", ""),
                analysis.get("mobile_feel", ""),
                analysis.get("what_to_steal", ""),
                analysis.get("notes", ""),
            ]
            ws.append_row(row)
            all_results.append({"site": site_type, "url": url, "analysis": analysis})
            print(f"  Logged to sheet: {analysis.get('style_vibe', '')} | Steal: {analysis.get('what_to_steal', '')}")

    print(f"\nDone. {len(all_results)} sites analyzed.")
    print(f"Sheet: https://docs.google.com/spreadsheets/d/{SHEET_ID}")
    print(f"Tab: {RESEARCH_TAB}")

    # Print summary for GitHub Action log
    print("\n=== RESEARCH SUMMARY ===")
    for r in all_results:
        a = r["analysis"]
        print(f"\n{r['site']} — {r['url']}")
        print(f"  Vibe: {a.get('style_vibe')}")
        print(f"  Colors: {a.get('colors')}")
        print(f"  Steal: {a.get('what_to_steal')}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--opc", default="", help="Comma-separated OPC reference URLs")
    parser.add_argument("--brazil", default="", help="Comma-separated Brazil RE reference URLs")
    parser.add_argument("--sheet", action="store_true", help="Read URLs from sheet instead")
    args = parser.parse_args()

    if args.sheet:
        # Read from Sheet — "Website Research Input" tab or Inbox
        spreadsheet = get_sheet()
        try:
            ws = spreadsheet.worksheet("Website Research Input")
            rows = ws.get_all_records()
            opc_urls = [r["URL"] for r in rows if r.get("Site") == "OPC" and r.get("URL")]
            brazil_urls = [r["URL"] for r in rows if r.get("Site") == "Brazil" and r.get("URL")]
        except Exception as e:
            print(f"Could not read from sheet: {e}")
            sys.exit(1)
    else:
        opc_urls = [u.strip() for u in args.opc.split(",") if u.strip()]
        brazil_urls = [u.strip() for u in args.brazil.split(",") if u.strip()]

    if not opc_urls and not brazil_urls:
        print("No URLs provided. Use --opc and/or --brazil flags, or --sheet to read from sheet.")
        sys.exit(1)

    process_urls(opc_urls, brazil_urls)


if __name__ == "__main__":
    main()
