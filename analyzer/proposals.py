from pathlib import Path
from . import cache, claude_client, research


def run_proposal_generation(count: int = 5, db_path: Path = cache.DB_PATH) -> list[dict]:
    clips = cache.get_all_clips(usable_only=True, db_path=db_path)
    if not clips:
        return []

    trend_context = research.get_trend_context_for_proposals(db_path=db_path)

    cache.clear_proposals(db_path=db_path)
    proposals = claude_client.generate_proposals(clips, count=count, trend_context=trend_context)

    if proposals:
        cache.save_proposals(proposals, db_path=db_path)

    return cache.get_proposals(db_path=db_path)
