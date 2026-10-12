"""
Temporal segment detector.
Samples 16 frames, finds motion/scene boundaries via diff-peak detection,
classifies each segment into a type + edit-use recommendation.
No Claude calls — purely OpenCV.
"""
import cv2
import numpy as np
import subprocess
from pathlib import Path


# ── Frame extraction ───────────────────────────────────────────────────────────

def _extract_segment_frames(video_path: str,
                             n: int = 16) -> list[tuple[float, np.ndarray]]:
    """Return [(timestamp_s, bgr_ndarray), …] at n evenly spaced points."""
    from .extractor import _ffmpeg, get_video_metadata, get_display_orientation, FRAME_TMP_DIR

    meta = get_video_metadata(video_path)
    duration = meta.get("duration_seconds", 0) or 10
    _, _, is_portrait = get_display_orientation(video_path)
    vf_scale = "scale=-2:256" if is_portrait else "scale=256:-2"
    FRAME_TMP_DIR.mkdir(parents=True, exist_ok=True)

    start = duration * 0.02
    end   = duration * 0.98
    timestamps = [start + (end - start) * i / (n - 1) for i in range(n)]

    result = []
    for i, ts in enumerate(timestamps):
        out = FRAME_TMP_DIR / f"_seg_{i:03d}.jpg"
        cmd = [_ffmpeg(), "-y", "-ss", str(ts), "-i", str(video_path),
               "-frames:v", "1", "-q:v", "4", "-vf", vf_scale, str(out)]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0 and out.exists():
            img = cv2.imread(str(out))
            if img is not None:
                result.append((ts, img))
            out.unlink(missing_ok=True)
    return result


# ── Boundary detection ─────────────────────────────────────────────────────────

def _find_boundaries(frames: list[tuple[float, np.ndarray]],
                     total: float,
                     min_seg_s: float = 4.0) -> list[float]:
    """
    Boundaries where consecutive-frame diff exceeds mean + 0.65 × std.
    Enforces minimum segment length to prevent micro-segments.
    """
    if len(frames) < 3:
        return []

    diffs = []
    for i in range(1, len(frames)):
        g0 = cv2.cvtColor(frames[i-1][1], cv2.COLOR_BGR2GRAY).astype(np.float32)
        g1 = cv2.cvtColor(frames[i  ][1], cv2.COLOR_BGR2GRAY).astype(np.float32)
        mid = (frames[i-1][0] + frames[i][0]) / 2
        diffs.append((mid, float(np.abs(g1 - g0).mean())))

    vals = [d[1] for d in diffs]
    threshold = float(np.mean(vals) + 0.65 * np.std(vals))

    boundaries: list[float] = []
    last = 0.0
    for ts, diff in diffs:
        if diff > threshold and ts - last >= min_seg_s:
            boundaries.append(round(ts, 1))
            last = ts
    return boundaries


# ── Per-segment metrics ────────────────────────────────────────────────────────

def _segment_metrics(frames_in_seg: list[np.ndarray]) -> dict:
    """Aggregate CV metrics across frames belonging to one segment."""
    from .local_cv import _frame_metrics, _normalize_to_landscape

    if not frames_in_seg:
        return {}

    per = [_frame_metrics(_normalize_to_landscape(f)) for f in frames_in_seg]
    agg: dict = {}
    for k in per[0]:
        agg[k] = float(np.mean([p[k] for p in per]))

    # Motion between consecutive pairs in this segment
    motion_diffs = []
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames_in_seg]
    for i in range(1, len(grays)):
        motion_diffs.append(float(cv2.absdiff(grays[i-1], grays[i]).mean()))
    agg["motion"] = float(np.mean(motion_diffs)) if motion_diffs else 0.0
    return agg


# ── Classification ─────────────────────────────────────────────────────────────

_COLOR_KEYS = {
    "Establishing Shot": "estab",
    "Wide Shot":         "estab",
    "Slow Pan":          "pan",
    "Hero Moment":       "hero",
    "Golden Hour":       "hero",
    "Detail / Texture":  "close",
    "Close-up":          "close",
    "Action":            "action",
    "Fast Motion":       "action",
    "B-roll":            "broll",
    "Static":            "broll",
}


def _motion_label(motion: float) -> str:
    if motion < 1.5:  return "Static"
    if motion < 4:    return "Drift"
    if motion < 8:    return "Pan"
    if motion < 15:   return "Active"
    return "Fast"


def _sharpness_label(blur: float) -> str:
    if blur > 300: return "Very sharp"
    if blur > 100: return "Sharp"
    if blur > 40:  return "Soft"
    return "Blurry"


def _lighting_label(m: dict) -> str:
    warm = m.get("warm_ratio", 0)
    bright = m.get("brightness", 127)
    sky = m.get("sky_ratio", 0)
    if warm > 0.22 and 70 < bright < 215:
        return "Golden hour — warm, directional"
    if bright < 45:
        return "Low light / dark"
    if bright > 220:
        return "Overexposed / bright day"
    if sky > 0.15:
        return "Open sky, natural"
    return "Diffused, even"


def _composition_label(m: dict) -> str:
    sky   = m.get("sky_ratio", 0)
    green = m.get("green_ratio", 0)
    water = m.get("water_ratio", 0)
    edge  = m.get("edge_density", 0)
    warm  = m.get("warm_ratio", 0)
    bright = m.get("brightness", 127)
    if sky > 0.3:
        return "Sky dominant (60%+), wide open"
    if sky > 0.15:
        return "Wide landscape, horizon visible"
    if water > 0.15:
        return "Water / coastal, lower-frame fill"
    if green > 0.3:
        return "Vegetation dominant, natural texture"
    if edge > 0.08:
        return "High detail, complex structure"
    if edge > 0.04:
        return "Mixed — subject + environment"
    if warm > 0.25 and bright > 100:
        return "Warm foreground, subject-focused"
    return "Neutral, balanced framing"


def _classify(m: dict, position: float) -> tuple[str, str]:
    """
    Returns (segment_type, edit_use).
    position: 0-1 — where this segment sits in the overall clip.
    """
    motion = m.get("motion", 0)
    warm   = m.get("warm_ratio", 0)
    green  = m.get("green_ratio", 0)
    sky    = m.get("sky_ratio", 0)
    edge   = m.get("edge_density", 0)
    blur   = m.get("blur", 0)
    bright = m.get("brightness", 127)
    water  = m.get("water_ratio", 0)

    is_sharp     = blur > 100
    is_golden    = warm > 0.2 and 70 < bright < 215
    is_wide      = sky > 0.12
    is_nature    = green > 0.22
    is_detail    = edge > 0.065 and motion < 5
    is_action    = motion > 9
    is_pan       = 4 <= motion <= 9
    is_static    = motion < 2
    is_water     = water > 0.12
    is_late      = position > 0.75
    is_early     = position < 0.25

    # Priority order
    if is_golden and is_sharp and not is_action:
        seg_type = "Hero Moment"
        use = "Hook / 3-sec"
    elif is_action:
        seg_type = "Action"
        use = "Mid-video energy"
    elif is_pan:
        seg_type = "Slow Pan"
        use = "Transition"
    elif is_detail:
        seg_type = "Detail / Texture"
        use = "B-roll cutaway"
    elif is_wide and is_late:
        seg_type = "Wide Shot"
        use = "Closing shot"
    elif is_wide and is_early:
        seg_type = "Establishing Shot"
        use = "Opening"
    elif is_wide:
        seg_type = "Establishing Shot"
        use = "B-roll / scene-set"
    elif is_nature:
        seg_type = "Nature"
        use = "B-roll cutaway"
    elif is_water:
        seg_type = "Water Shot"
        use = "B-roll / scene-set"
    elif is_static and is_late:
        seg_type = "Wide Shot"
        use = "Closing shot"
    elif is_static and is_early:
        seg_type = "Establishing Shot"
        use = "Opening"
    else:
        seg_type = "B-roll"
        use = "B-roll cutaway"

    return seg_type, use


def _make_notes(seg_type: str, use: str, m: dict, duration_s: float) -> str:
    motion  = m.get("motion", 0)
    warm    = m.get("warm_ratio", 0)
    blur    = m.get("blur", 0)
    bright  = m.get("brightness", 127)
    sky     = m.get("sky_ratio", 0)
    edge    = m.get("edge_density", 0)

    dur_s = round(duration_s)

    if seg_type == "Hero Moment":
        return (
            f"Warm light ({round(warm*100)}% warm pixels) and sharp focus make this the "
            f"strongest segment. Use the first 3 s as an opening hook before text appears, "
            f"or cut to it at a beat drop. {dur_s} s total — trim to 2–4 s for Reels/TikTok."
        )
    if seg_type == "Action":
        return (
            f"High motion ({round(motion, 1)} diff/frame) — sync to a chorus or percussion hit. "
            f"Cut short ({min(dur_s, 3)}–{min(dur_s, 5)} s); avoid freeze-framing due to motion blur. "
            f"Works best mid-video after the hook has landed."
        )
    if seg_type == "Slow Pan":
        return (
            f"Consistent movement ({round(motion, 1)} diff/frame) with no shake — clean wipe-replacement. "
            f"Match pan speed to a beat. Strong bridge between two static clips; cut on the mid-point "
            f"of the pan to carry momentum."
        )
    if seg_type == "Detail / Texture":
        return (
            f"Dense structure (edge density {round(edge*100, 1)}%) and low motion ({round(motion,1)}) "
            f"make this a strong insert. Cut to it for 1–2 s over voiceover to add visual rhythm. "
            f"Keep it brief — it lacks narrative movement."
        )
    if seg_type in ("Establishing Shot", "Wide Shot") and use == "Opening":
        return (
            f"Sky coverage ({round(sky*100)}%) and {'static ' if motion < 2 else 'gentle '}framing "
            f"set the scene. Hold 3–5 s before the first cut. Works as an opening title card background "
            f"or to establish a new location."
        )
    if seg_type in ("Wide Shot", "Establishing Shot") and "Closing" in use:
        return (
            f"{'Static' if motion < 2 else 'Low-motion'} and sky-dominant — natural sense of conclusion. "
            f"End on this before a title card or fade-to-black. {dur_s} s available; "
            f"3–4 s is usually enough."
        )
    if seg_type == "B-roll cutaway" or use == "B-roll cutaway":
        return (
            f"Neutral content — good filler over narration or music. "
            f"Short cut (1–3 s) to break up talking-head or static sections. "
            f"Sharpness is {'good' if blur > 100 else 'moderate'}; re-evaluate if used larger than 50%."
        )
    # Fallback
    return (
        f"{'Static' if motion < 2 else 'Moving'} segment, "
        f"{'warm tones' if warm > 0.15 else 'neutral tones'}, "
        f"{'sharp' if blur > 100 else 'soft'} focus. "
        f"Can be used as {use.lower()} at {dur_s} s; trim to taste."
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def analyze_clip_segments(video_path: str) -> list[dict] | None:
    """
    Detect and classify temporal segments of a video clip.
    Returns a list of segment dicts ready for JSON storage and UI rendering.
    Returns None on failure.
    """
    from .extractor import get_video_metadata

    meta = get_video_metadata(video_path)
    total = meta.get("duration_seconds") or 0
    if total < 3:
        return None

    n_frames = min(20, max(8, int(total / 2)))
    frames = _extract_segment_frames(video_path, n=n_frames)
    if len(frames) < 2:
        return None

    boundaries = _find_boundaries(frames, total)
    # Add clip start and end
    cut_points = [0.0] + boundaries + [round(total, 1)]

    segments = []
    for i in range(len(cut_points) - 1):
        seg_start = cut_points[i]
        seg_end   = cut_points[i + 1]
        seg_dur   = seg_end - seg_start

        # Frames whose timestamp falls in [seg_start, seg_end)
        seg_frames = [img for ts, img in frames
                      if seg_start <= ts < seg_end]
        if not seg_frames:
            # Grab nearest frame
            nearest = min(frames, key=lambda x: abs(x[0] - (seg_start + seg_dur / 2)))
            seg_frames = [nearest[1]]

        m = _segment_metrics(seg_frames)
        position = (seg_start + seg_dur / 2) / total
        seg_type, use = _classify(m, position)

        segments.append({
            "start":        round(seg_start, 1),
            "end":          round(seg_end, 1),
            "type":         seg_type,
            "use":          use,
            "color_key":    _COLOR_KEYS.get(seg_type, "broll"),
            "motion":       round(min(1.0, m.get("motion", 0) / 20), 3),
            "motion_raw":   round(m.get("motion", 0), 2),
            "motion_label": _motion_label(m.get("motion", 0)),
            "lighting":     _lighting_label(m),
            "composition":  _composition_label(m),
            "sharpness":    _sharpness_label(m.get("blur", 0)),
            "notes":        _make_notes(seg_type, use, m, seg_dur),
        })

    return segments if segments else None
