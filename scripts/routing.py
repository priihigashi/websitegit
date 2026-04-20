"""
routing.py — Single source of truth for all niche/project routing.

Read by: capture_pipeline.py, capture_queue_processor.py,
         content_tracker.py, approval_handler.py

Add a new niche here → every script instantly knows where to route it.
Never hardcode spreadsheet IDs or drive IDs in individual scripts.
"""

# fmt: off

ROUTES = {
    # ── News niches ────────────────────────────────────────────────────────────
    "brazil": {
        "label":              "Brazil News",
        "pipeline":           "brazil",   # canonical — must match capture_pipeline.yml choice list
        "drive_id":           "0AH7_C87G0ZwgUk9PVA",
        "drive_name":         "News",
        "capture_folder_id":  "1DZWbS4bF4XF_OjJSnD02WD2N83ljXwHd",  # News/Brazil/Captures
        "carousel_folder_id": "1gDOjtW_X-_jWtu94pffbDaUsw6VGCKuA",  # News/Brazil/Carousel (v1_name_seriestopic/{png,motion})
        "reels_folder_id":    "1IY4TJyv9Dk1qJPdhskyn4flj1g1jp0Kl",  # News/Brazil/Reels_Shorts
        "content_control_id": "1QFHa_xcuLOqbbYbtzeMVhb5ypfHIbAkVJyyInCKlgcM",
        "content_control_tab":"🇧🇷 Brazil In Production",
        "published_tab":      "✅ Published",
        "queue_dest":         "Brazil News Drive",
        "story_prefix":       "NWS",
        "email_label":        "News capture done (Brazil)",
    },
    "usa": {
        "label":              "USA News",
        "pipeline":           "usa",     # canonical — must match capture_pipeline.yml choice list
        "drive_id":           "0AH7_C87G0ZwgUk9PVA",
        "drive_name":         "News",
        "capture_folder_id":  "1ZzrEmj3Smt0chr8CxiCOyroFCRzE-zU1",  # News/USA/Captures
        "carousel_folder_id": "1lRfZE5XC_gL57pUiiWu0Lhar9wfyCtFw",  # News/USA/Carousel (v1_name_seriestopic/{png,motion})
        "reels_folder_id":    "1EN2HhPzmUnwjXhXpaaf1hO52REAo7wB0",  # News/USA/Reels_Shorts
        "content_control_id": "1QFHa_xcuLOqbbYbtzeMVhb5ypfHIbAkVJyyInCKlgcM",
        "content_control_tab":"🇺🇸 USA In Production",
        "published_tab":      "✅ Published",
        "queue_dest":         "USA News Drive",
        "story_prefix":       "NWS",
        "email_label":        "News capture done (USA)",
    },
    # ── OPC ────────────────────────────────────────────────────────────────────
    "opc": {
        "label":              "OPC",
        "pipeline":           "opc",
        "drive_id":           "0AIPzwsJD_qqzUk9PVA",
        "drive_name":         "Marketing",
        "capture_folder_id":  "1lyWGwQiUPAVoMzb8vfQ0fBw72M1A2UfR",  # Marketing/Content/Captures (new, confirmed 2026-04-20)
        "carousel_folder_id": "1j_wiygaY0ltLkOp9-etiDDsA4R4n5ecm",  # Marketing/Content/Series (v1_name_seriestopic/{png,motion})
        "reels_folder_id":    "1jW3WUQEPpfJNgje-4YGyFT4inKgzWrt7",  # Marketing/Content/Reels_Shorts
        "content_control_id": "1C1CAZ8lSgeVLSSCYIg-D9XPJcSLHyIOh1okKtvhZZQg",
        "content_control_tab":"🎬 In Production",
        "published_tab":      "✅ Published",
        "queue_dest":         "Marketing Content Drive",
        "story_prefix":       "CNT",
        "email_label":        "OPC capture done",
    },
    # ── UGC ────────────────────────────────────────────────────────────────────
    "ugc": {
        "label":              "UGC",
        "pipeline":           "ugc",
        "drive_id":           "0AEz0NlGr3tlLUk9PVA",
        "drive_name":         "UGC",
        "capture_folder_id":  "1b5fCmWn6cUkZSjhaZKGFmaKDc4MafY3U",  # UGC/Captures
        "content_control_id": "1yVUcXbq085eB-vC-ieL1vkblfLOkvIW_R9AU_I4a1TY",
        "content_control_tab":"🎬 In Production",
        "published_tab":      "✅ Published",
        "queue_dest":         "UGC Drive",
        "story_prefix":       "UGC",
        "email_label":        "UGC capture done",
    },
    # ── Stocks ─────────────────────────────────────────────────────────────────
    "stocks": {
        "label":              "Stocks",
        "pipeline":           "stocks",
        "drive_id":           "0AF6S_f8PH2_aUk9PVA",
        "drive_name":         "Stocks",
        "capture_folder_id":  "17oazrbMM1lBeFAGNCaFp8sjnAMWbVdSI",  # Stocks/Captures
        "content_control_id": "1eeAgy70rxit4_WJN-msArg9mHVLZqpnFVHcDPnpTZ9c",
        "content_control_tab":"🎬 In Production",
        "published_tab":      "✅ Published",
        "queue_dest":         "Stocks Drive",
        "story_prefix":       "STK",
        "email_label":        "Stocks capture done",
    },
    # ── Higashi ────────────────────────────────────────────────────────────────
    "higashi": {
        "label":              "Higashi",
        "pipeline":           "higashi",
        "drive_id":           "0AN7aea2IZzE0Uk9PVA",
        "drive_name":         "Higashi",
        "capture_folder_id":  "1by4guSe46XK0DwIJwmNUEtbzmvQFOXOv",  # Higashi/Captures
        "content_control_id": "1yrGU3Y8AdthtxkLqhL31taqHjjBPRND8lh8qKkV5iKo",
        "content_control_tab":"🎬 In Production",
        "published_tab":      "✅ Published",
        "queue_dest":         "Higashi Drive",
        "story_prefix":       "HIG",
        "email_label":        "Higashi capture done",
    },
    # ── Book ───────────────────────────────────────────────────────────────────
    "book": {
        "label":              "Book",
        "pipeline":           "book",
        "drive_id":           "0AH7_C87G0ZwgUk9PVA",
        "drive_name":         "News",
        "capture_folder_id":  "15_mV965QoGsi3Y9gd45NDiUiig4eeGe9",  # News/Book/Captures
        "content_control_id": "",
        "content_control_tab":"",
        "published_tab":      "",
        "queue_dest":         "Book Tracker",
        "story_prefix":       "BCI",
        "email_label":        "Book capture done",
    },
}

# Legacy aliases — map to canonical keys (kept ONLY for backward compat with old queue rows / old workflow inputs)
_ALIASES = {
    "news":    "brazil",   # "news" = Brazil News
    "content": "opc",
}

# fmt: on


def get_route(niche_or_project: str) -> dict:
    """Return routing config for a niche/project key. Case-insensitive. Falls back to opc."""
    key = niche_or_project.lower().strip()
    key = _ALIASES.get(key, key)
    return ROUTES.get(key, ROUTES["opc"])


def pipeline_project(queue_project: str) -> str:
    """Map a Capture Queue project value to the capture_pipeline.py --project arg."""
    return get_route(queue_project)["pipeline"]


def queue_dest(queue_project: str) -> str:
    """Human-readable destination label for the Capture Queue 'moved to' column."""
    return get_route(queue_project)["queue_dest"]


def content_control(niche: str) -> tuple[str, str]:
    """Return (spreadsheet_id, tab_name) for the In Production tracker of this niche."""
    r = get_route(niche)
    return r["content_control_id"], r["content_control_tab"]


def capture_folder(project: str) -> str:
    """Return the Drive folder ID where captures for this project should be saved."""
    return get_route(project)["capture_folder_id"]
