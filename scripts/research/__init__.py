"""scripts/research/ — shared research helpers.

Modules:
- transcription.py        : URL -> transcript cascade (YouTube, Instagram, TikTok)
- candidate_collectors.py : keyword -> candidate URLs (YouTube, Instagram)
- evidence_scoring.py     : transcript -> rubric score against requirement

Used by: scripts/youtube_research.py (--mode person_evidence_mining)
Future:  scripts/capture/capture_pipeline.py refactor (Phase 1.5)
"""
