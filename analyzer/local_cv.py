"""
Tier-1 analysis: pure OpenCV, zero API cost.
Returns a result dict + confidence score (0-1).
Low confidence signals the caller to escalate to CLIP or Claude.
"""
import cv2
import numpy as np
from pathlib import Path


def _frame_metrics(img_bgr: np.ndarray) -> dict:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    hsv  = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:,:,0], hsv[:,:,1], hsv[:,:,2]
    H, W = img_bgr.shape[:2]

    blur       = cv2.Laplacian(gray, cv2.CV_64F).var()
    brightness = float(gray.mean())
    contrast   = float(gray.std())

    # warm pixels: hue 0-30 or 155-180 (reds/oranges), decent saturation+value
    warm_mask = ((h <= 30) | (h >= 155)) & (s > 70) & (v > 70)
    warm_ratio = float(warm_mask.mean())

    # green pixels: hue 35-85
    green_mask = (h > 35) & (h < 85) & (s > 50) & (v > 50)
    green_ratio = float(green_mask.mean())

    # sky: upper 30% of frame, blue-ish hue 90-130
    upper = hsv[:int(H * 0.3), :, :]
    sky_mask = (upper[:,:,0] > 90) & (upper[:,:,0] < 130) & (upper[:,:,1] > 30)
    sky_ratio = float(sky_mask.mean())

    # edge density (urban/architecture indicator)
    edges = cv2.Canny(gray, 80, 180)
    edge_density = float(edges.mean() / 255.0)

    # blue water: hue 90-130 in lower half
    lower = hsv[int(H * 0.4):, :, :]
    water_mask = (lower[:,:,0] > 90) & (lower[:,:,0] < 130) & (lower[:,:,1] > 40) & (lower[:,:,2] > 40)
    water_ratio = float(water_mask.mean())

    return dict(blur=blur, brightness=brightness, contrast=contrast,
                warm_ratio=warm_ratio, green_ratio=green_ratio,
                sky_ratio=sky_ratio, edge_density=edge_density,
                water_ratio=water_ratio)


def _aggregate(frame_metrics_list: list[dict]) -> dict:
    keys = frame_metrics_list[0].keys()
    agg = {}
    for k in keys:
        vals = [m[k] for m in frame_metrics_list]
        agg[k] = float(np.mean(vals))
        if k == 'blur':
            agg['blur_min'] = float(np.min(vals))
    return agg


def _motion_score(frame_paths: list[Path]) -> float:
    scores = []
    prev = None
    for fp in frame_paths:
        img = cv2.imread(str(fp))
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if prev is not None:
            scores.append(float(cv2.absdiff(gray, prev).mean()))
        prev = gray
    return float(np.mean(scores)) if scores else 0.0


def analyze_frames_cv(frame_paths: list[Path]) -> dict | None:
    """
    Returns dict with keys matching the DB schema + 'confidence' (0-1) + '_metrics'.
    Returns None if frames can't be read.
    """
    per_frame = []
    for fp in frame_paths:
        img = cv2.imread(str(fp))
        if img is not None:
            per_frame.append(_frame_metrics(img))
    if not per_frame:
        return None

    m = _aggregate(per_frame)
    m['motion'] = _motion_score(frame_paths)

    # ── Quality score ──────────────────────────────────────────────────────────
    blur = m['blur']
    if blur < 25:
        quality_score = max(1, int(blur / 25 * 3))
        is_usable = False
    elif blur < 80:
        quality_score = 3 + int((blur - 25) / 55 * 2)
        is_usable = True
    elif blur < 250:
        quality_score = 5 + int((blur - 80) / 170 * 2)
        is_usable = True
    else:
        quality_score = min(10, 7 + int((blur - 250) / 300 * 3))
        is_usable = True

    # Exposure penalty
    brightness = m['brightness']
    if brightness < 35:
        quality_score = max(1, quality_score - 3)
        is_usable = False
    elif brightness > 235:
        quality_score = max(2, quality_score - 2)

    quality_score = int(np.clip(quality_score, 1, 10))

    # ── Lighting ───────────────────────────────────────────────────────────────
    warm_ratio = m['warm_ratio']
    if warm_ratio > 0.22 and 70 < brightness < 210:
        lighting = 'golden_hour'
    elif brightness < 45:
        lighting = 'dark'
    elif brightness > 210:
        lighting = 'bright_day'
    elif m['sky_ratio'] > 0.08:
        lighting = 'bright_day'
    else:
        lighting = 'overcast'

    # ── Category signal strengths ──────────────────────────────────────────────
    signals = {}

    if warm_ratio > 0.2 and lighting == 'golden_hour':
        signals['golden-hour'] = warm_ratio * 2.5

    if m['green_ratio'] > 0.18:
        signals['nature'] = m['green_ratio'] * 2.0

    if m['motion'] > 6:
        signals['action'] = min(1.0, m['motion'] / 15)

    if m['sky_ratio'] > 0.12 and m['motion'] < 8:
        signals['travel'] = m['sky_ratio'] * 1.8

    if m['water_ratio'] > 0.12:
        signals['travel'] = max(signals.get('travel', 0), m['water_ratio'] * 1.5)

    if m['edge_density'] > 0.055:
        signals['architecture'] = m['edge_density'] * 8

    if not signals:
        signals['b-roll'] = 0.25

    category = max(signals, key=signals.get)
    top = signals[category]

    # Confidence: gap between top signal and runner-up
    sorted_vals = sorted(signals.values(), reverse=True)
    if len(sorted_vals) > 1:
        gap = sorted_vals[0] - sorted_vals[1]
        confidence = min(0.95, gap / max(sorted_vals[0], 0.01) * 0.9 + 0.1)
    else:
        confidence = 0.6 if top > 0.4 else 0.35

    # Penalise confidence when signals are weak
    if top < 0.3:
        confidence = min(confidence, 0.35)

    # ── Scene type ─────────────────────────────────────────────────────────────
    if m['sky_ratio'] > 0.18:
        scene_type = 'landscape'
    elif m['green_ratio'] > 0.3:
        scene_type = 'forest'
    elif m['water_ratio'] > 0.15:
        scene_type = 'water'
    elif m['edge_density'] > 0.06:
        scene_type = 'urban'
    elif brightness < 55:
        scene_type = 'indoor'
    else:
        scene_type = 'landscape'

    # ── Mood ───────────────────────────────────────────────────────────────────
    if m['motion'] > 9:
        mood = 'energetic'
    elif lighting == 'golden_hour' and m['contrast'] > 45:
        mood = 'dramatic'
    elif lighting == 'golden_hour':
        mood = 'cinematic'
    elif m['green_ratio'] > 0.2 and m['motion'] < 3:
        mood = 'peaceful'
    elif brightness < 70:
        mood = 'dramatic'
    else:
        mood = 'cinematic'

    return {
        'quality_score':  quality_score,
        'is_usable':      is_usable,
        'category':       category,
        'scene_type':     scene_type,
        'mood':           mood,
        'lighting':       lighting,
        'activities':     [],
        'notes':          None,
        'confidence':     round(confidence, 3),
        '_metrics':       m,
    }
