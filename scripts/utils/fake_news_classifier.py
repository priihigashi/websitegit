"""
fake_news_classifier.py — Shared utilities for fake-news detection across the pipeline.
Used by: capture_pipeline.py, sheets_writer.py, 4am_agent/topic_scraper.py
"""
from urllib.parse import urlparse


def normalize_url(url: str) -> str:
    """Strip query params and fragments for deduplication.
    'https://www.instagram.com/reel/ABC/?igsh=xyz' → 'https://www.instagram.com/reel/ABC/'
    """
    if not url:
        return ""
    p = urlparse(url.strip())
    return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")


def classify_fake_news(text: str, url: str = "") -> dict:
    """Lightweight heuristic classifier. Returns fields for Inspiration Library columns AA-AC.

    Returns:
        {
            "is_fake_news": bool,
            "series_override": "VERIFICAMOS" | "FACT-CHECKED" | "",
            "fake_news_route": "A" | "B",
            "fake_news_confidence": float (0.0-1.0)
        }

    Route A = original claim clip exists (source_clip available)
    Route B = no clip, use expert debunk / institutional source carousel

    NOTE: Full LLM-based classification lives in capture_pipeline.py::analyze_content().
    This heuristic is used by the scraper for items ingested without a transcript.
    """
    indicators = [
        "fake news", "desinformação", "desinformacao", "mentira", "falso",
        "verificamos", "fact-check", "checagem", "checamos", "aos fatos",
        "afp", "lupa", "viralizou", "boato", "desmentido", "falsa informação",
        "é falso", "e falso", "nao é verdade", "nao e verdade",
    ]
    text_lower = text.lower()
    score = sum(1 for kw in indicators if kw in text_lower)

    is_fake_news = score >= 2
    confidence = min(0.4 + (score * 0.1), 0.9)

    # Determine series from URL niche
    series_override = ""
    if is_fake_news:
        # Instagram reels from Brazilian accounts → VERIFICAMOS; others → FACT-CHECKED
        if "instagram.com" in url or "tiktok.com" in url:
            series_override = "VERIFICAMOS"
        else:
            series_override = "FACT-CHECKED"

    # Route A only if we have a clip URL (caller must override if transcript present)
    fake_news_route = "B"

    return {
        "is_fake_news": is_fake_news,
        "series_override": series_override,
        "fake_news_route": fake_news_route,
        "fake_news_confidence": round(confidence, 2),
    }
