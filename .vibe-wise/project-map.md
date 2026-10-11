# Project Map

## Purpose
Footage: scan a local folder of raw video clips, analyze/categorize them using a 3-tier local-first CV cascade, and generate social media content proposals.

## Requirements
- Fast scan (metadata + thumbnail only, no Claude)
- Manual categorization in the UI, Claude called per-clip on demand
- 3-tier analysis: OpenCV → CLIP → Claude text-only (user confirmation before Claude)
- Token budget: 1-tile frames (512px, 3 frames = ~5k tokens/clip)
- No API billing: uses `claude -p` subprocess (Pro quota)

## Components
- `server.py` — FastAPI on port 7842; SSE streaming for scan, reanalyze, proposals, trends
- `static/index.html` — Single-page UI; GSAP animations; dark film palette
- `analyzer/extractor.py` — ffmpeg/imageio-ffmpeg frame + thumbnail extraction
- `analyzer/local_cv.py` — Tier 1: OpenCV blur/color/motion analysis + confidence score
- `analyzer/clip_analyzer.py` — Tier 2: CLIP zero-shot classification (optional, needs open-clip-torch)
- `analyzer/claude_client.py` — Tier 3a: Claude text-only; Tier 3b: Claude full vision
- `analyzer/cache.py` — SQLite (data/clips.db); clips, proposals, trends, groups, custom_categories
- `analyzer/research.py` — Trend fetching via Claude
- `data/thumbs/` — Per-clip JPEG thumbnails keyed by MD5

## Main Flow
```
User enters folder → POST /api/scan (SSE)
  → extractor.find_videos()
  → per clip: md5, metadata, thumbnail → upsert(analyzed_at=None)

User clicks Analyze → POST /api/clips/{id}/reanalyze?mode=auto (SSE)
  → Tier 1: local_cv.analyze_frames_cv() → confidence
    ≥0.55 → save + complete
    <0.55 → Tier 2: clip_analyzer.analyze_frames_clip()
      ≥0.50 → merge CV quality metrics → save + complete
      <0.50 → needs_confirmation SSE → UI shows confirm bar
        User confirms → re-POST mode=claude_text
          → cv metrics → claude_client.analyze_clip_text_only() → save + complete
```

## Data and Trust Boundaries
- All data local: SQLite, thumbs dir, /tmp frames
- Claude only reached via `claude -p` subprocess (Pro quota, no API key)
- No auth; single-user local tool

## Build and Deployment
- `python server.py` or `uvicorn server:app --port 7842 --reload`
- venv at project root; `pip install -r requirements.txt`
- Optional Tier 2: `pip install open-clip-torch` (~350MB)

## Unknowns
- CLIP model not yet tested on real clips (library install not confirmed)
- Confidence thresholds (0.55/0.50) untested; may need tuning
