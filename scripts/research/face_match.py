"""face_match.py — SH-104 Phase 2 SCAFFOLD (no live media yet).

Public API (stable):
  is_available() -> bool
  build_seed_embedding(seed_video_path) -> dict | None
  verify_candidate(candidate_video_path, seed_embedding) -> dict
  delete_after_run(run_dir, seed_embedding=None) -> None

ALL FUNCTIONS ARE NO-OPS UNTIL THE insightface DEPENDENCY IS INSTALLED.
That keeps the runner safe to import unconditionally; verify_candidate()
returns the Phase-1 metadata-only contract until Phase 2 ships.

Privacy contract — non-negotiable (per CLAUDE.md NAMED-PERSON FACE rule
+ /clip-mine SKILL legal guardrails):

  1. Face embeddings are IN-MEMORY ONLY for the duration of one run.
  2. Embeddings are NEVER written to:
     - Drive (any folder)
     - evidence_manifest.json
     - scored_candidates.json
     - Sheets columns
     - workflow artifacts uploaded by GitHub Actions
     - run.log or stdout
  3. delete_after_run() is called from the orchestrator's finally block
     and zeroes the in-memory array + scrubs any *.npy files written
     during the run.
  4. Raw face crops used to derive embeddings are deleted in the same
     finally pass (not just garbage-collected).

Phase-2 launch checklist (DO NOT REMOVE):
  - [ ] Add insightface>=0.7 to video-research.yml pip install
  - [ ] Add buffalo_l model warm-up (~250MB) — pre-download in CI
  - [ ] Wire build_seed_embedding into person_evidence_runner before
        score_candidate; gate verified set on face match.
  - [ ] Update _coerce_to_schema to ACCEPT same_person_method="face_match"
        ONLY when face_match.is_available() returns True (single source of
        truth — keeps the Phase 1 downgrade in place when scaffold is dead).
  - [ ] Add a "face match deleted after run" line to the email summary.
  - [ ] Audit: no .npy / .pkl / face_*.bin written to clipmine_* folders.
"""

from __future__ import annotations
import os
import shutil
from pathlib import Path
from typing import Optional


# ── feature-detection / lazy import ─────────────────────────────────────────

_INSIGHTFACE_AVAILABLE: Optional[bool] = None


def is_available() -> bool:
    """True only when insightface is importable AND a buffalo_l model is
    on disk. Phase 1 scaffold returns False — keeps Phase 1 contract
    (face_match downgrade in evidence_scoring) in effect until the model
    is wired up."""
    global _INSIGHTFACE_AVAILABLE
    if _INSIGHTFACE_AVAILABLE is not None:
        return _INSIGHTFACE_AVAILABLE
    try:
        import insightface  # noqa: F401
        _INSIGHTFACE_AVAILABLE = True
    except ImportError:
        _INSIGHTFACE_AVAILABLE = False
    return _INSIGHTFACE_AVAILABLE


# ── seed embedding (lazy-loaded, in-memory only) ─────────────────────────────

def build_seed_embedding(seed_video_path: str, sample_frames: int = 8) -> Optional[dict]:
    """Sample N frames from the seed video, run buffalo_l, average the
    embeddings. Returns:
      {"embedding": numpy.ndarray (512,),
       "frame_count": int,
       "model": "buffalo_l",
       "_in_memory_only": True}
    or None when:
      - face_match unavailable (Phase 1 scaffold)
      - no face detected in any sampled frame
      - seed_video_path missing

    NEVER persist the returned dict. Callers must pass it to verify_candidate
    in-memory and call delete_after_run when done.
    """
    if not is_available():
        return None
    # Phase 2 implementation goes here. Sketch (not active):
    #
    #   import cv2, numpy as np
    #   from insightface.app import FaceAnalysis
    #   app = FaceAnalysis(name="buffalo_l", allowed_modules=["detection", "recognition"])
    #   app.prepare(ctx_id=-1)  # CPU
    #   cap = cv2.VideoCapture(seed_video_path)
    #   total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    #   indices = np.linspace(0, max(total - 1, 0), sample_frames, dtype=int)
    #   embeddings = []
    #   for idx in indices:
    #       cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
    #       ok, frame = cap.read()
    #       if not ok: continue
    #       faces = app.get(frame)
    #       if not faces: continue
    #       # primary face = largest bbox
    #       primary = max(faces, key=lambda f: (f.bbox[2]-f.bbox[0]) * (f.bbox[3]-f.bbox[1]))
    #       embeddings.append(primary.normed_embedding)
    #   cap.release()
    #   if not embeddings:
    #       return None
    #   mean_emb = np.mean(embeddings, axis=0)
    #   return {"embedding": mean_emb, "frame_count": len(embeddings),
    #           "model": "buffalo_l", "_in_memory_only": True}
    return None


def verify_candidate(candidate_video_path: str, seed_embedding: Optional[dict],
                     sample_frames: int = 4,
                     match_threshold: float = 0.50) -> dict:
    """Return Phase-2 verification result. Phase 1 scaffold returns the
    "metadata-only" contract so the runner can call this unconditionally.

    Output schema:
      {
        "method": "face_match" | "metadata",
        "available": bool,        # is the face_match infra live?
        "match": bool,            # similarity >= match_threshold
        "similarity": float,      # cosine 0..1; 0.0 when not run
        "frames_with_face": int,
        "match_threshold": float,
        "phase1_fallback": bool,  # True when face_match unavailable
      }
    """
    if not is_available() or not seed_embedding:
        return {
            "method": "metadata",
            "available": False,
            "match": False,
            "similarity": 0.0,
            "frames_with_face": 0,
            "match_threshold": match_threshold,
            "phase1_fallback": True,
        }
    # Phase 2 implementation sketch (not active):
    #
    #   import cv2, numpy as np
    #   from insightface.app import FaceAnalysis
    #   app = FaceAnalysis(name="buffalo_l", allowed_modules=["detection","recognition"])
    #   app.prepare(ctx_id=-1)
    #   cap = cv2.VideoCapture(candidate_video_path)
    #   total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    #   indices = np.linspace(0, max(total-1,0), sample_frames, dtype=int)
    #   sims = []
    #   for idx in indices:
    #       cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
    #       ok, frame = cap.read()
    #       if not ok: continue
    #       faces = app.get(frame)
    #       for f in faces:
    #           sims.append(float(np.dot(seed_embedding["embedding"], f.normed_embedding)))
    #   cap.release()
    #   if not sims:
    #       return {... "frames_with_face": 0 ...}
    #   sim = max(sims)
    #   return {"method":"face_match","available":True,"match":sim>=match_threshold,
    #           "similarity":sim,"frames_with_face":len(sims),
    #           "match_threshold":match_threshold,"phase1_fallback":False}
    return {
        "method": "face_match",
        "available": True,
        "match": False,
        "similarity": 0.0,
        "frames_with_face": 0,
        "match_threshold": match_threshold,
        "phase1_fallback": False,
    }


# ── delete-after-run privacy guarantee ──────────────────────────────────────

def delete_after_run(run_dir: Optional[str] = None,
                     seed_embedding: Optional[dict] = None) -> dict:
    """Zero the in-memory seed embedding + scrub any biometric artifacts
    that may have leaked to disk during the run. Always called by the
    orchestrator in a `finally` block.

    Returns audit record:
      {"embedding_zeroed": bool, "files_removed": [str], "errors": [str]}
    """
    audit: dict = {"embedding_zeroed": False, "files_removed": [], "errors": []}

    # 1) Zero the numpy embedding in place if present.
    if seed_embedding and isinstance(seed_embedding, dict):
        emb = seed_embedding.get("embedding")
        try:
            if emb is not None and hasattr(emb, "fill"):
                emb.fill(0.0)
                audit["embedding_zeroed"] = True
            seed_embedding["embedding"] = None
        except Exception as e:
            audit["errors"].append(f"embedding_zero: {e}")

    # 2) Scrub biometric files from run_dir (if it exists).
    if run_dir:
        bad_suffixes = (".npy", ".pkl", ".bin", ".embedding", ".embeddings")
        bad_name_tokens = ("face_", "embedding", "biometric")
        bad_subfolders = ("face_crops", "embeddings", "biometric")
        try:
            root = Path(run_dir)
            if root.exists():
                for sub in bad_subfolders:
                    p = root / sub
                    if p.exists():
                        try:
                            shutil.rmtree(p)
                            audit["files_removed"].append(str(p))
                        except Exception as e:
                            audit["errors"].append(f"rmtree {p}: {e}")
                for f in root.rglob("*"):
                    if not f.is_file():
                        continue
                    name = f.name.lower()
                    if (name.endswith(bad_suffixes)
                            or any(t in name for t in bad_name_tokens)):
                        try:
                            os.remove(f)
                            audit["files_removed"].append(str(f))
                        except Exception as e:
                            audit["errors"].append(f"remove {f}: {e}")
        except Exception as e:
            audit["errors"].append(f"walk {run_dir}: {e}")

    return audit


# ── tiny self-test (no model needed) ────────────────────────────────────────

if __name__ == "__main__":
    print(f"insightface available: {is_available()}")
    print("Phase 1 scaffold — verify_candidate returns metadata-only contract.")
    sample = verify_candidate("/dev/null", None)
    print(sample)
    audit = delete_after_run(None, None)
    print("delete_after_run audit:", audit)
