"""
Tier-2 analysis: CLIP zero-shot image classification.
Lazy import — requires open-clip-torch (pip install open-clip-torch).
~350MB one-time model download on first use. Zero tokens.
"""
from pathlib import Path

_MODEL = None
_PREPROCESS = None
_TOKENIZER = None

# Multiple prompts per category — averaged embeddings improve accuracy.
# b-roll and 'other' are excluded: they describe the *use* of footage, not
# visual content, so CLIP has no reliable signal for them. OpenCV handles
# the b-roll fallback; Claude handles 'other'.
_PROMPTS_MULTI = {
    'travel': [
        'travel photography showing mountains, coastlines, or scenic roads',
        'a tourist exploring a foreign landscape or city street',
        'wide shot of a natural or urban travel destination',
    ],
    'golden-hour': [
        'golden hour photography with warm orange sunset light',
        'the sun setting over a landscape bathed in amber glow',
        'magic hour backlit scene with long warm shadows at dusk or dawn',
    ],
    'action': [
        'action sports footage showing fast movement or physical activity',
        'someone running, surfing, skiing, or performing an athletic activity',
        'high-energy camera movement following a moving subject',
    ],
    'nature': [
        'nature photography of trees, forests, or wildlife in the wild',
        'lush green plants and vegetation in natural outdoor light',
        'a wildlife animal or bird in its natural habitat',
    ],
    'portrait': [
        'a close-up portrait of a person looking at the camera',
        'a person\'s face or upper body as the main subject of the shot',
        'a group of people posing or interacting in a social scene',
    ],
    'food': [
        'food photography of a plated meal or dish',
        'a colorful plate of food or a drink being prepared or served',
        'close-up of ingredients or a meal on a table or in a kitchen',
    ],
    'architecture': [
        'architecture photography of a building facade or urban structure',
        'city street photography showing buildings and infrastructure',
        'geometric patterns in modern or historic architectural design',
    ],
}

_CLIP_CATEGORIES = set(_PROMPTS_MULTI.keys())

_MOOD_HINTS = {
    'golden-hour': 'cinematic', 'action': 'energetic', 'nature': 'peaceful',
    'travel': 'cinematic', 'portrait': 'intimate', 'food': 'peaceful',
    'architecture': 'dramatic',
}
_SCENE_HINTS = {
    'golden-hour': 'landscape', 'action': 'outdoor', 'nature': 'forest',
    'travel': 'landscape', 'portrait': 'indoor', 'food': 'indoor',
    'architecture': 'urban',
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
    quality_score / is_usable / lighting stay None (CLIP can't assess these).
    Returns None if open_clip not installed or no frames loadable.
    """
    if not _load_model():
        return None

    try:
        import torch
        from PIL import Image
    except ImportError:
        return None

    from .cache import BASE_CATEGORIES
    # Only include visually concrete categories — skip b-roll and other
    all_cats = [c for c in (extra_categories or []) + BASE_CATEGORIES
                if c in _CLIP_CATEGORIES]
    if not all_cats:
        return None

    with torch.no_grad():
        # Average text embeddings across multiple prompts per category
        cat_feats = {}
        for cat in all_cats:
            prompts = _PROMPTS_MULTI.get(cat, [f'video footage of {cat}'])
            tokens = _TOKENIZER(prompts)
            feats = _MODEL.encode_text(tokens)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            cat_feats[cat] = feats.mean(0)

        text_matrix = torch.stack([cat_feats[c] for c in all_cats])
        text_matrix = text_matrix / text_matrix.norm(dim=-1, keepdim=True)

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
        probs = (avg @ text_matrix.T).squeeze(0).softmax(dim=0).cpu().tolist()

    scores = dict(zip(all_cats, probs))
    category = max(scores, key=scores.get)
    sorted_vals = sorted(scores.values(), reverse=True)
    gap = sorted_vals[0] - sorted_vals[1] if len(sorted_vals) > 1 else sorted_vals[0]
    confidence = round(min(0.95, gap * 4.0), 3)

    # b-roll winning at low confidence = CLIP is confused, not actual b-roll
    # (b-roll is excluded from all_cats above, so this is a safety net)
    if category == 'b-roll' and confidence < 0.6:
        confidence = min(confidence, 0.3)

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
