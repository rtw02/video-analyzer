import sys
import json
import asyncio
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
from analyzer import cache, extractor, claude_client, research as research_module
from analyzer.local_cv import analyze_frames_cv
from analyzer.clip_analyzer import analyze_frames_clip

CV_CONFIDENCE_THRESHOLD   = 0.55
CLIP_CONFIDENCE_THRESHOLD = 0.50

cache.init_db()

app = FastAPI(title="Footage")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


# ── Clips ──────────────────────────────────────────────────────────────────────

def _fmt_clip(c: dict) -> dict:
    dur = c.get("duration_seconds") or 0
    mins = int(dur) // 60
    secs = int(dur) % 60
    res = c.get("resolution", "")
    res_display = res
    if "x" in res:
        w_str = res.split("x")[0]
        if w_str.isdigit():
            w = int(w_str)
            res_display = "4K" if w >= 3840 else "1080p" if w >= 1920 else "720p" if w >= 1280 else res
    activities = []
    try:
        activities = json.loads(c.get("activities") or "[]")
    except Exception:
        pass
    thumb_path = Path("data/thumbs") / f"{c.get('md5_hash','x')}.jpg"
    return {
        "id": c["id"],
        "name": c["filename"],
        "filepath": c.get("filepath", ""),
        "md5": c.get("md5_hash", ""),
        "duration_str": f"{mins}:{secs:02d}",
        "duration_seconds": dur,
        "resolution": res,
        "res_display": res_display,
        "quality_score": c.get("quality_score") or 0,
        "scene_type": c.get("scene_type") or "",
        "mood": c.get("mood") or "",
        "lighting": c.get("lighting") or "",
        "category": c.get("category") or "other",
        "is_usable": bool(c.get("is_usable", 1)),
        "notes": c.get("notes") or "",
        "activities": activities,
        "has_thumb": thumb_path.exists(),
    }


@app.get("/api/clips")
async def get_clips():
    clips = cache.get_all_clips()
    return {"clips": [_fmt_clip(c) for c in clips]}


class ManualUpdate(BaseModel):
    category: str | None = None
    quality_score: int | None = None
    scene_type: str | None = None
    mood: str | None = None
    lighting: str | None = None
    is_usable: bool | None = None
    notes: str | None = None


@app.patch("/api/clips/{clip_id}")
async def update_clip_manual(clip_id: int, body: ManualUpdate):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "is_usable" in fields:
        fields["is_usable"] = int(fields["is_usable"])
    if fields:
        fields["analyzed_at"] = datetime.now().isoformat()
        cache.update_clip_manual(clip_id, fields)
    return {"ok": True}


@app.get("/api/clips/{clip_id}/thumb")
async def get_thumb(clip_id: int):
    conn = cache.get_connection()
    row = conn.execute("SELECT md5_hash FROM clips WHERE id = ?", (clip_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    p = Path("data/thumbs") / f"{row['md5_hash']}.jpg"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p), media_type="image/jpeg")


@app.delete("/api/clips/{clip_id}")
async def delete_clip(clip_id: int):
    cache.delete_clip(clip_id)
    return {"ok": True}


class CategoryUpdate(BaseModel):
    category: str


@app.patch("/api/clips/{clip_id}/category")
async def update_clip_category(clip_id: int, body: CategoryUpdate):
    cache.set_clip_category(clip_id, body.category)
    return {"ok": True}


class ReanalyzeBody(BaseModel):
    mode: str = "auto"  # auto | cv | clip | claude_text | claude_full


@app.post("/api/clips/{clip_id}/reanalyze")
async def reanalyze_clip(clip_id: int, body: ReanalyzeBody | None = None):
    mode = body.mode if body else "auto"
    conn = cache.get_connection()
    row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404)
    row = dict(row)

    async def generate():
        loop = asyncio.get_event_loop()
        video_path = Path(row["filepath"])
        if not video_path.exists():
            yield f"data: {json.dumps({'type':'error','error':'File not found'})}\n\n"
            return

        yield f"data: {json.dumps({'type':'analyzing','file':row['filename'],'mode':mode})}\n\n"
        custom_cats = [c["name"] for c in cache.get_custom_categories()]
        frame_paths = await loop.run_in_executor(None, extractor.extract_frames, str(video_path))

        result = None
        tier_used = None
        cv_metrics = None
        cv_result = None

        # ── Tier 1: OpenCV ────────────────────────────────────────────────────
        if mode in ("auto", "cv"):
            cv_result = await loop.run_in_executor(None, lambda: analyze_frames_cv(frame_paths))
            if cv_result:
                cv_metrics = cv_result.get("_metrics")
                confidence = cv_result.get("confidence", 0)
                yield f"data: {json.dumps({'type':'cv_result','confidence':confidence,'category':cv_result.get('category')})}\n\n"
                if mode == "cv" or confidence >= CV_CONFIDENCE_THRESHOLD:
                    result = cv_result
                    tier_used = "cv"

        # ── Tier 2: CLIP ──────────────────────────────────────────────────────
        if result is None and mode in ("auto", "clip"):
            clip_result = await loop.run_in_executor(
                None, lambda: analyze_frames_clip(frame_paths, extra_categories=custom_cats)
            )
            if clip_result:
                confidence = clip_result.get("confidence", 0)
                yield f"data: {json.dumps({'type':'clip_result','confidence':confidence,'category':clip_result.get('category')})}\n\n"
                if mode == "clip" or confidence >= CLIP_CONFIDENCE_THRESHOLD:
                    # Merge quality metrics from CV (CLIP can't assess sharpness/exposure)
                    if cv_result:
                        clip_result["quality_score"] = cv_result.get("quality_score")
                        clip_result["is_usable"]     = cv_result.get("is_usable")
                        clip_result["lighting"]      = clip_result.get("lighting") or cv_result.get("lighting")
                    result = clip_result
                    tier_used = "clip"

        # ── Auto cascade dead-end: ask user before using Claude ───────────────
        if result is None and mode == "auto":
            extractor.cleanup_frames(frame_paths)
            yield f"data: {json.dumps({'type':'needs_confirmation','metrics':cv_metrics or {}})}\n\n"
            return

        # ── Tier 3a: Claude text-only (~250 tokens) ───────────────────────────
        if result is None and mode == "claude_text":
            if not cv_metrics:
                cv_r = await loop.run_in_executor(None, lambda: analyze_frames_cv(frame_paths))
                cv_metrics = cv_r.get("_metrics") if cv_r else {}
            analysis = await loop.run_in_executor(
                None, lambda: claude_client.analyze_clip_text_only(cv_metrics or {}, custom_categories=custom_cats)
            )
            result = analysis
            tier_used = "claude_text"

        # ── Tier 3b: Claude full vision (~5000 tokens) ────────────────────────
        if result is None and mode == "claude_full":
            analysis = await loop.run_in_executor(
                None, lambda: claude_client.analyze_frames(frame_paths, custom_categories=custom_cats)
            )
            result = analysis
            tier_used = "claude_full"

        extractor.cleanup_frames(frame_paths)

        if not result:
            yield f"data: {json.dumps({'type':'error','error':'Analysis failed'})}\n\n"
            return

        updated = dict(row)
        updated.update({
            "analyzed_at":  datetime.now().isoformat(),
            "quality_score": result.get("quality_score"),
            "scene_type":    result.get("scene_type"),
            "mood":          result.get("mood"),
            "lighting":      result.get("lighting"),
            "activities":    json.dumps(result.get("activities", [])),
            "category":      result.get("category"),
            "is_usable":     int(result.get("is_usable", True)) if result.get("is_usable") is not None else 1,
            "notes":         result.get("notes"),
            "raw_analysis":  json.dumps(result),
        })
        cache.upsert_clip(updated)
        yield f"data: {json.dumps({'type':'complete','analysis':result,'tier':tier_used})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Scan ───────────────────────────────────────────────────────────────────────

class ScanBody(BaseModel):
    folder: str


@app.post("/api/scan")
async def scan(body: ScanBody):
    folder = body.folder.strip()
    if not folder or not Path(folder).is_dir():
        raise HTTPException(400, "Invalid folder path")

    async def generate():
        loop = asyncio.get_event_loop()
        files = extractor.find_videos(folder)
        total = len(files)
        yield f"data: {json.dumps({'type':'start','total':total})}\n\n"
        custom_cats = [c["name"] for c in cache.get_custom_categories()]
        thumb_dir = Path("data/thumbs")
        thumb_dir.mkdir(parents=True, exist_ok=True)

        for i, video_path in enumerate(files):
            md5 = await loop.run_in_executor(None, cache.compute_md5, str(video_path))
            existing = cache.get_clip_by_hash(md5)
            if existing and existing.get("analyzed_at"):
                yield f"data: {json.dumps({'type':'cached','file':video_path.name,'index':i+1,'total':total})}\n\n"
                await asyncio.sleep(0)
                continue

            yield f"data: {json.dumps({'type':'scanning','file':video_path.name,'index':i+1,'total':total})}\n\n"
            try:
                meta = await loop.run_in_executor(None, extractor.get_video_metadata, str(video_path))
                await loop.run_in_executor(None, extractor.extract_thumbnail, str(video_path), thumb_dir, md5)
                clip_data = {
                    "filepath": str(video_path),
                    "filename": video_path.name,
                    "md5_hash": md5,
                    "duration_seconds": meta.get("duration_seconds"),
                    "resolution": meta.get("resolution"),
                    "file_size_mb": meta.get("file_size_mb"),
                    "analyzed_at": None,
                    "quality_score": None,
                    "scene_type": None,
                    "mood": None,
                    "lighting": None,
                    "activities": "[]",
                    "category": "other",
                    "is_usable": 1,
                    "notes": None,
                    "raw_analysis": None,
                }
                cache.upsert_clip(clip_data)
                yield f"data: {json.dumps({'type':'done','file':video_path.name,'index':i+1,'total':total})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type':'error','file':video_path.name,'error':str(e),'index':i+1,'total':total})}\n\n"
            await asyncio.sleep(0)

        yield f"data: {json.dumps({'type':'complete'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Proposals ─────────────────────────────────────────────────────────────────

@app.get("/api/proposals")
async def get_proposals():
    props = cache.get_proposals()
    result = []
    for p in props:
        result.append({
            "id": p["id"],
            "title": p.get("title"),
            "platform": p.get("platform"),
            "estimated_duration": p.get("estimated_duration"),
            "clip_ids": json.loads(p.get("clip_ids") or "[]"),
            "hook_text": p.get("hook_text"),
            "caption": p.get("caption"),
            "hashtags": json.loads(p.get("hashtags") or "[]"),
            "vibe": p.get("vibe"),
            "status": p.get("status"),
        })
    return {"proposals": result}


@app.post("/api/proposals/generate")
async def generate_proposals():
    clips = cache.get_all_clips(usable_only=True, analyzed_only=True)
    if not clips:
        raise HTTPException(400, "No analyzed clips")

    async def generate():
        loop = asyncio.get_event_loop()
        trend_ctx = research_module.get_trend_context_for_proposals()
        props = await loop.run_in_executor(
            None, lambda: claude_client.generate_proposals(clips, count=5, trend_context=trend_ctx)
        )
        if props:
            cache.save_proposals(props)
        yield f"data: {json.dumps({'type':'complete','count':len(props)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class StatusUpdate(BaseModel):
    status: str


@app.patch("/api/proposals/{proposal_id}/status")
async def update_proposal_status(proposal_id: int, body: StatusUpdate):
    cache.update_proposal_status(proposal_id, body.status)
    return {"ok": True}


# ── Trends ─────────────────────────────────────────────────────────────────────

@app.get("/api/trends")
async def get_trends():
    trends = cache.get_cached_trends()
    age = cache.get_trends_age()
    return {"trends": trends or [], "age": age}


@app.post("/api/trends/fetch")
async def fetch_trends():
    async def generate():
        loop = asyncio.get_event_loop()
        trends = await loop.run_in_executor(None, research_module.fetch_trends)
        yield f"data: {json.dumps({'type':'complete','count':len(trends)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Categories ─────────────────────────────────────────────────────────────────

@app.get("/api/categories")
async def get_categories():
    return {"base": cache.BASE_CATEGORIES, "custom": cache.get_custom_categories()}


class CategoryCreate(BaseModel):
    name: str
    color: str = "#888888"


@app.post("/api/categories")
async def add_category(body: CategoryCreate):
    cache.add_custom_category(body.name, body.color)
    return {"ok": True}


@app.delete("/api/categories/{name}")
async def delete_category(name: str):
    cache.delete_custom_category(name)
    return {"ok": True}


# ── Groups ─────────────────────────────────────────────────────────────────────

@app.get("/api/groups")
async def get_groups():
    return {"groups": cache.get_groups()}


class GroupCreate(BaseModel):
    name: str
    group_type: str = "custom"
    description: str = ""


@app.post("/api/groups")
async def create_group(body: GroupCreate):
    gid = cache.create_group(body.name, body.group_type, body.description)
    return {"id": gid}


@app.get("/api/groups/{group_id}")
async def get_group(group_id: int):
    g = cache.get_group(group_id)
    if not g:
        raise HTTPException(404)
    return g


@app.delete("/api/groups/{group_id}")
async def delete_group_endpoint(group_id: int):
    cache.delete_group(group_id)
    return {"ok": True}


class GroupRename(BaseModel):
    name: str


@app.patch("/api/groups/{group_id}")
async def rename_group(group_id: int, body: GroupRename):
    cache.rename_group(group_id, body.name)
    return {"ok": True}


class ClipAdd(BaseModel):
    clip_id: int


@app.post("/api/groups/{group_id}/clips")
async def add_clip_to_group(group_id: int, body: ClipAdd):
    cache.add_clip_to_group(group_id, body.clip_id)
    return {"ok": True}


@app.delete("/api/groups/{group_id}/clips/{clip_id}")
async def remove_clip_from_group(group_id: int, clip_id: int):
    cache.remove_clip_from_group(group_id, clip_id)
    return {"ok": True}


class ReorderBody(BaseModel):
    clip_ids: list[int]


@app.post("/api/groups/{group_id}/reorder")
async def reorder_group(group_id: int, body: ReorderBody):
    cache.reorder_group(group_id, body.clip_ids)
    return {"ok": True}


@app.post("/api/groups/{group_id}/analyze")
async def analyze_group(group_id: int):
    g = cache.get_group(group_id)
    if not g:
        raise HTTPException(404)

    async def generate():
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: claude_client.analyze_group_sequence(g["clips"])
        )
        if "error" not in result:
            cache.update_group_analysis(
                group_id,
                result.get("arc", ""),
                result.get("edit_notes", ""),
                result.get("transitions", []),
            )
        yield f"data: {json.dumps({'type':'complete','result':result})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=7842, reload=True)
