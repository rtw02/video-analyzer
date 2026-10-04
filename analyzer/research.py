import json
import re
from pathlib import Path
from . import cache, claude_client

TREND_PROMPT = """Search the web for what is currently trending in travel and lifestyle social media videos
(TikTok, Instagram Reels, YouTube Shorts) right now in {month_year}.

Find and summarize:
1. Top trending video formats and storytelling styles for travel content
2. Trending sounds or audio styles performing well on travel/lifestyle videos
3. Popular visual effects, transitions, or editing techniques
4. Hook formats and caption styles getting high engagement

Return ONLY a valid JSON array (no other text, no markdown), each item:
{{
  "name": "short trend name",
  "platform": "tiktok|reels|youtube-shorts|all",
  "category": "format|sound|effect|hook",
  "description": "what it is and why it works (2 sentences max)",
  "relevance_to_travel": "how to specifically apply this to travel footage (1 sentence)",
  "popularity": "trending|rising|established",
  "view_indicator": "approximate views or uses if found, else empty string"
}}

Aim for 12-16 trends total, covering all 4 categories."""


def fetch_trends(db_path: Path = cache.DB_PATH) -> list[dict]:
    """Fetch fresh trends via Claude web search, cache in DB, return list."""
    from datetime import datetime
    month_year = datetime.now().strftime("%B %Y")
    prompt = TREND_PROMPT.format(month_year=month_year)

    msg = claude_client._build_stream_json_message([{"type": "text", "text": prompt}])

    # allow WebSearch tool so Claude can actually look things up
    import subprocess
    cmd = [
        "claude", "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--allowedTools", "WebSearch",
    ]
    result = subprocess.run(cmd, input=msg, capture_output=True, text=True, timeout=180)

    if result.returncode != 0 or not result.stdout.strip():
        return []

    text = claude_client._parse_stream_json_output(result.stdout)
    if not text:
        return []

    try:
        data = claude_client._extract_json(text)
        trends = data if isinstance(data, list) else []
    except (json.JSONDecodeError, ValueError):
        return []

    if trends:
        cache.save_trends(trends, db_path=db_path)

    return trends


def get_trends(force_refresh: bool = False, db_path: Path = cache.DB_PATH) -> list[dict]:
    """Return trends from cache if fresh (<24h), else fetch new ones."""
    if not force_refresh:
        cached = cache.get_cached_trends(max_age_hours=24, db_path=db_path)
        if cached:
            return cached
    return fetch_trends(db_path=db_path)


def get_trend_context_for_proposals(db_path: Path = cache.DB_PATH) -> str:
    """Return top trends as a compact string to inject into proposal prompts."""
    trends = cache.get_cached_trends(max_age_hours=48, db_path=db_path) or []
    if not trends:
        return ""
    lines = []
    for t in trends[:8]:  # top 8 to keep prompt size reasonable
        lines.append(f"- [{t.get('platform','all')}] {t.get('name')}: {t.get('description','')}")
    return "Currently trending on social media:\n" + "\n".join(lines)
