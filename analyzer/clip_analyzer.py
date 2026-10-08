"""
Tier-2 analysis: CLIP zero-shot image classification.
Lazy import — requires open-clip-torch (pip install open-clip-torch).
~350MB one-time model download on first use. Zero tokens.
"""
from pathlib import Path

_MODEL = None
_PREPROCESS = None
_TOKENIZER = None

# Per-category text prompts fed to CLIP
_PROMPTS = {
    'travel':        'travel photography of landscapes, mountains, roads, or scenic views',
    'golden-hour':   'golden hour sunset or sunrise with warm orange and amber light',
    'b-roll':        'generic b-roll filler footage or cutaway video clips',
    'action':        'action sports footage with movement, speed, or physical activity',
    'nature':        'nature photography of plants, trees, forests, or wildlife',
    'portrait':      'a portrait photograph of a person or group of people',
    'food':          'food photography, a meal, or cooking being prepared',
    'architecture':  'architecture photography of buildings, structures, or city streets',
    'other':         'miscellaneous footage that does not fit specific categories',
}

_MOOD_HINTS = {
    'golden-hour': 'cinematic', 'action': 'energetic', 'nature': 'peaceful',
    'travel': 'cinematic', 'portrait': 'intimate', 'b-roll': 'cinematic',
    'food': 'peaceful', 'architecture': 'dramatic', 'other': 'cinematic',
}
_SCENE_HINTS = {
    'golden-hour': 'landscape', 'action': 'outdoor', 'nature': 'forest',
    'travel': 'landscape', 'portrait': 'indoor', 'b-roll': 'landscape',
    'food': 'indoor', 'architecture': 'urban', 'other': 'landscape',
}


def is_available() -> bool:
    try:
        import open_clip  # noqa: F401
        return True
    except ImportError:
        return False


def _load_model() -> bool:
    global _MODEL, _PREPROCESS, _TOKENIZER
    if _MODEL is not None:
        return True
    try:
        import open_clip
        model, _, preprocess = open_clip.create_model_and_transforms(
            'ViT-B-32', pretrained='openai'
        )
        model.eval()
        _MODEL = model
        _PREPROCESS = preprocess
        _TOKENIZER = open_clip.get_tokenizer('ViT-B-32')
        return True
    except Exception:
        return False


def analyze_frames_clip(frame_paths: list[Path],
                        extra_categories: list[str] | None = None) -> dict | None:
    """
    Zero-shot classify frames via CLIP.
    Returns same-shape dict as local_cv + 'confidence' (0-1) + '_scores'.
    quality_score / is_usable / lighting stay None (CLIP can't determine these).
    Returns None if open_clip not installed or no frames loadable.
    """
    if not _load_model():
        return None

    try:
        import torch
        from PIL import Image
    except ImportError:
        return None

    # Build category list — custom first, then base fallbacks
    from .cache import BASE_CATEGORIES
    all_cats = list(dict.fromkeys((extra_categories or []) + BASE_CATEGORIES))

    prompts = [_PROMPTS.get(c, f'video footage of {c}') for c in all_cats]

    with torch.no_grad():
        text_tokens = _TOKENIZER(prompts)
        text_feats = _MODEL.encode_text(text_tokens)
        text_feats = text_feats / text_feats.norm(dim=-1, keepdim=True)

        img_feats = []
        for fp in frame_paths:
            try:
                img = _PREPROCESS(Image.open(fp).convert('RGB')).unsqueeze(0)
                f = _MODEL.encode_image(img)
                f = f / f.norm(dim=-1, keepdim=True)
                img_feats.append(f)
            except Exception:
                continue

        if not img_feats:
            return None

        avg = torch.stack(img_feats).mean(0)
        avg = avg / avg.norm(dim=-1, keepdim=True)
        probs = (avg @ text_feats.T).squeeze(0).softmax(dim=0).cpu().tolist()

    scores = dict(zip(all_cats, probs))
    category = max(scores, key=scores.get)
    sorted_vals = sorted(scores.values(), reverse=True)
    gap = sorted_vals[0] - sorted_vals[1] if len(sorted_vals) > 1 else sorted_vals[0]
    confidence = round(min(0.95, gap * 4.0), 3)  # gap 0.25 → 1.0, gap 0.125 → 0.5

    return {
        'quality_score': None,
        'is_usable':     None,
        'category':      category,
        'scene_type':    _SCENE_HINTS.get(category, 'landscape'),
        'mood':          _MOOD_HINTS.get(category, 'cinematic'),
        'lighting':      None,
        'activities':    [],
        'notes':         None,
        'confidence':    confidence,
        '_scores': {c: round(s, 3) for c, s in
                    sorted(scores.items(), key=lambda x: -x[1])[:5]},
    }
