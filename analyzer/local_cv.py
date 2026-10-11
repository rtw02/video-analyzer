"""
Tier-1 analysis: pure OpenCV, zero API cost.
Returns a result dict + confidence score (0-1).
Low confidence signals the caller to escalate to CLIP or Claude.
"""
import cv2
import numpy as np
from pathlib import Path


def _normalize_to_landscape(img_bgr: np.ndarray) -> np.ndarray:
    """Rotate portrait frames 90° CW so zone-based analysis stays consistent."""
    H, W = img_bgr.shape[:2]
    if H > W:
        return cv2.rotate(img_bgr, cv2.ROTATE_90_CLOCKWISE)
    return img_bgr


def _frame_metrics(img_bgr: np.ndarray) -> dict:
    img_bgr = _normalize_to_landscape(img_bgr)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    hsv  = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:,:,0], hsv[:,:,1], hsv[:,:,2]
    H, W = img_bgr.shape[:2]

    blur       = cv2.Laplacian(gray, cv2.CV_64F).var()
    brightness = float(gray.mean())
    contrast   = float(gray.std())

    warm_mask = ((h <= 30) | (h >= 155)) & (s > 70) & (v > 70)
    warm_ratio = float(warm_mask.mean())

    green_mask = (h > 35) & (h < 85) & (s > 50) & (v > 50)
    green_ratio = float(green_mask.mean())

    upper = hsv[:int(H * 0.3), :, :]
    sky_mask = (upper[:,:,0] > 90) & (upper[:,:,0] < 130) & (upper[:,:,1] > 30)
    sky_ratio = float(sky_mask.mean())

    edges = cv2.Canny(gray, 80, 180)
    edge_density = float(edges.mean() / 255.0)

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


def _motion_score(grays: list[np.ndarray]) -> tuple[float, str]:
    """
    Returns (magnitude, motion_type) from pre-loaded grayscale frames.
    motion_type: 'static' | 'pan' | 'shake' | 'action'
    High std-dev relative to magnitude = shake (handheld jitter, bad is_usable).
    """
    scores = []
    for i in range(1, len(grays)):
        scores.append(float(cv2.absdiff(grays[i - 1], grays[i]).mean()))

    if not scores:
        return 0.0, 'static'

    magnitude = float(np.mean(scores))
    variance  = float(np.std(scores))

    if magnitude < 1.5:
        motion_type = 'static'
    elif variance > magnitude * 0.8:
        motion_type = 'shake'
    elif magnitude > 8:
        motion_type = 'action'
    else:
        motion_type = 'pan'

    return magnitude, motion_type


def _face_score(grays: list[np.ndarray], bgrs: list[np.ndarray]) -> tuple[bool, float]:
    """
    Returns (has_face, score 0-1) from pre-loaded frames.
    Score scales with the largest detected face area relative to frame size.
    """
    try:
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        )
        best = 0.0
        for gray in grays:
            H, W = gray.shape
            faces = cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=4, minSize=(30, 30)
            )
            if len(faces) > 0:
                areas = [w * h for (x, y, w, h) in faces]
                largest = max(areas) / (H * W)
                score = min(1.0, largest * 10 + 0.3 * len(faces))
                best = max(best, score)
        return best > 0, best
    except Exception:
        return False, 0.0


def analyze_frames_cv(frame_paths: list[Path]) -> dict | None:
    """
    Returns dict matching DB schema + 'confidence' (0-1) + '_metrics'.
    Each frame is loaded exactly once; BGR arrays are passed to all sub-analyzers.
    Returns None if frames can't be read.
    """
    bgrs: list[np.ndarray] = []
    grays: list[np.ndarray] = []
    per_frame = []

    for fp in frame_paths:
        img = cv2.imread(str(fp))
        if img is None:
            continue
        bgrs.append(img)
        grays.append(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        per_frame.append(_frame_metrics(img))

    if not per_frame:
        return None

    m = _aggregate(per_frame)
    motion_magnitude, motion_type = _motion_score(grays)
    m['motion'] = motion_magnitude
    m['motion_type'] = motion_type
    has_face, face_score = _face_score(grays, bgrs)

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

    brightness = m['brightness']
    if brightness < 35:
        quality_score = max(1, quality_score - 3)
        is_usable = False
    elif brightness > 235:
        quality_score = max(2, quality_score - 2)

    # Shake = bad footage
    if motion_type == 'shake':
        quality_score = max(1, quality_score - 2)
        is_usable = quality_score >= 4

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

    # ── Category signals with hierarchy ───────────────────────────────────────
    # Priority: portrait > action (strong) > golden-hour > nature > travel > architecture > b-roll

    signals = {}

    # Portrait: face detection wins over everything
    if has_face:
        signals = {'portrait': min(1.0, face_score)}

    else:
        action_score = min(1.0, motion_magnitude / 15) if motion_magnitude > 6 else 0

        # Strong action suppresses landscape/color signals
        if action_score > 0.5:
            signals = {'action': action_score}

        else:
            if action_score > 0:
                signals['action'] = action_score

            # Golden hour: warm + correct lighting
            if warm_ratio > 0.2 and lighting == 'golden_hour':
                signals['golden-hour'] = warm_ratio * 2.5

            # Nature: penalized when golden-hour already dominant
            if m['green_ratio'] > 0.18:
                gh_penalty = 0.5 if 'golden-hour' in signals else 1.0
                signals['nature'] = m['green_ratio'] * 2.0 * gh_penalty

            if m['sky_ratio'] > 0.12 and motion_magnitude < 8:
                signals['travel'] = m['sky_ratio'] * 1.8

            if m['water_ratio'] > 0.12:
                signals['travel'] = max(signals.get('travel', 0), m['water_ratio'] * 1.5)

            if m['edge_density'] > 0.055:
                signals['architecture'] = m['edge_density'] * 8

            if not signals:
                signals['b-roll'] = 0.25

    category = max(signals, key=signals.get)
    top = signals[category]

    sorted_vals = sorted(signals.values(), reverse=True)
    if len(sorted_vals) > 1:
        gap = sorted_vals[0] - sorted_vals[1]
        confidence = min(0.95, gap / max(sorted_vals[0], 0.01) * 0.9 + 0.1)
    else:
        # Single signal: scale confidence to strength, cap at 0.75
        confidence = min(0.75, top * 0.9)

    if top < 0.3:
        confidence = min(confidence, 0.35)

    # ── Scene type ─────────────────────────────────────────────────────────────
    if has_face:
        scene_type = 'portrait'
    elif m['sky_ratio'] > 0.18:
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
    if motion_type in ('action', 'shake') and motion_magnitude > 9:
        mood = 'energetic'
    elif lighting == 'golden_hour' and m['contrast'] > 45:
        mood = 'dramatic'
    elif lighting == 'golden_hour':
        mood = 'cinematic'
    elif m['green_ratio'] > 0.2 and motion_magnitude < 3:
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
