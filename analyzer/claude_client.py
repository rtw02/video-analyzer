import subprocess
import json
import re
import base64
from pathlib import Path

from .cache import BASE_CATEGORIES

def _build_analyze_prompt(custom_categories: list[str] | None = None) -> str:
    cats = BASE_CATEGORIES[:]
    if custom_categories:
        cats = custom_categories + [c for c in BASE_CATEGORIES if c not in custom_categories]
    cat_str = ", ".join(cats)
    return f"""Analyze these video frames and return ONLY valid JSON (no other text, no markdown):
{{
  "scene_type": "one of: landscape, urban, indoor, water, mountain, beach, city, forest, desert, other",
  "quality_score": integer 1-10,
  "mood": "one of: cinematic, energetic, peaceful, dramatic, intimate, adventurous, romantic, other",
  "lighting": "one of: golden_hour, blue_hour, bright_day, overcast, indoor, dark, harsh, silhouette",
  "activities": ["list", "of", "activities or subjects visible"],
  "category": "one of: {cat_str}",
  "is_usable": true or false (false only if severely shaky, badly out of focus, or too dark to see),
  "notes": "one sentence about this clip social media potential"
}}"""

GROUP_SEQUENCE_PROMPT = """You are a video editor specializing in travel and lifestyle content.
I have a group of clips I want to arrange into a coherent sequence or timeline.
Analyze the clips and suggest the optimal edit order.

Clips in this group:
{clips_json}

Return ONLY valid JSON (no other text, no markdown):
{{
  "suggested_order": [list of clip id integers in suggested playback order],
  "transitions": [
    {{"from_clip": clip_id_int, "to_clip": clip_id_int, "note": "brief transition description, e.g. cut on motion / dissolve / match cut"}},
    ...one entry per consecutive pair...
  ],
  "arc": "one sentence describing the emotional or narrative arc of this sequence",
  "edit_notes": "2-3 sentences on pacing, rhythm, and any editing suggestions",
  "estimated_total_duration": "approximate total runtime if edited"
}}"""

PROPOSAL_PROMPT = """You are a social media video editor specializing in travel and lifestyle content.
Given these video clips from my personal library, propose {count} specific video ideas.
Focus on what would perform best on social media: strong hooks, visual storytelling, platform-specific formats.

Clips:
{clips_json}

Return ONLY a valid JSON array (no other text, no markdown), each item:
{{
  "title": "short descriptive title",
  "platform": "one of: tiktok, reels, youtube-shorts, youtube",
  "estimated_duration": "e.g. 30s or 2min",
  "clip_ids": [list of clip id integers to use in suggested order],
  "hook_text": "compelling opening hook for caption or voiceover",
  "caption": "full social media caption draft",
  "hashtags": ["relevant", "hashtags"],
  "vibe": "e.g. cinematic travel, energetic adventure, peaceful nature"
}}"""


def _encode_image(path: Path) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _build_stream_json_message(content_blocks: list) -> str:
    return json.dumps({
        "type": "user",
        "message": {
            "role": "user",
            "content": content_blocks
        }
    })


def _parse_stream_json_output(stdout: str) -> str | None:
    for line in stdout.strip().split("\n"):
        if not line:
            continue
        try:
            obj = json.loads(line)
            if obj.get("type") == "assistant":
                content = obj.get("message", {}).get("content", [])
                for block in content:
                    if block.get("type") == "text":
                        return block["text"]
        except json.JSONDecodeError:
            continue
    return None


def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _run_claude(input_json: str, timeout: int = 90) -> str | None:
    cmd = [
        "claude", "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
    ]
    result = subprocess.run(cmd, input=input_json, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        # retry once
        result = subprocess.run(cmd, input=input_json, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        return None
    return _parse_stream_json_output(result.stdout)


def analyze_frames(frame_paths: list[Path], timeout: int = 90,
                   custom_categories: list[str] | None = None) -> dict:
    if not frame_paths:
        return {}

    prompt = _build_analyze_prompt(custom_categories)
    content = []
    for fp in frame_paths:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": _encode_image(fp)
            }
        })
    content.append({"type": "text", "text": prompt})

    msg = _build_stream_json_message(content)
    text = _run_claude(msg, timeout=timeout)

    if not text:
        return {"error": "no response from claude"}

    try:
        return _extract_json(text)
    except json.JSONDecodeError:
        return {"error": f"bad JSON: {text[:200]}"}


def analyze_group_sequence(clips: list[dict], timeout: int = 90) -> dict:
    """Given an ordered list of clip dicts, suggest sequence order + transitions."""
    summaries = []
    for c in clips:
        summaries.append({
            "id": c["id"],
            "filename": c["filename"],
            "duration_seconds": c.get("duration_seconds"),
            "scene_type": c.get("scene_type"),
            "mood": c.get("mood"),
            "lighting": c.get("lighting"),
            "category": c.get("category"),
            "quality_score": c.get("quality_score"),
            "activities": json.loads(c.get("activities") or "[]"),
            "notes": c.get("notes"),
        })

    prompt = GROUP_SEQUENCE_PROMPT.format(clips_json=json.dumps(summaries, indent=2))
    msg = _build_stream_json_message([{"type": "text", "text": prompt}])
    text = _run_claude(msg, timeout=timeout)

    if not text:
        return {"error": "no response from claude"}

    try:
        return _extract_json(text)
    except json.JSONDecodeError:
        return {"error": f"bad JSON: {text[:200]}"}


def generate_proposals(clips: list[dict], count: int = 5, timeout: int = 120, trend_context: str = "") -> list[dict]:
    clip_summaries = []
    for c in clips:
        clip_summaries.append({
            "id": c["id"],
            "filename": c["filename"],
            "duration_seconds": c.get("duration_seconds"),
            "scene_type": c.get("scene_type"),
            "mood": c.get("mood"),
            "lighting": c.get("lighting"),
            "category": c.get("category"),
            "quality_score": c.get("quality_score"),
            "activities": json.loads(c.get("activities") or "[]"),
            "notes": c.get("notes"),
        })

    trend_section = f"\n\n{trend_context}" if trend_context else ""
    prompt = PROPOSAL_PROMPT.format(
        count=count,
        clips_json=json.dumps(clip_summaries, indent=2)
    ) + trend_section

    msg = _build_stream_json_message([{"type": "text", "text": prompt}])
    text = _run_claude(msg, timeout=timeout)

    if not text:
        return []

    try:
        data = _extract_json(text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []
