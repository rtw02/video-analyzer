import streamlit as st
import json
from pathlib import Path

from analyzer import cache, extractor, claude_client, proposals, research

DB_PATH = Path(__file__).parent / "data" / "clips.db"
THUMBS_DIR = Path(__file__).parent / "data" / "thumbs"

cache.init_db(DB_PATH)

st.set_page_config(page_title="Video Analyzer", layout="wide", page_icon="🎬")
st.title("🎬 Video Analyzer")

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Scan Folder")
    folder_input = st.text_input("Folder path", placeholder="/Users/you/Videos/trip")
    col1, col2 = st.columns(2)
    scan_btn = col1.button("Scan", use_container_width=True, type="primary")
    proposal_btn = col2.button("Propose", use_container_width=True)

    st.divider()
    st.header("Filters")
    show_unusable = st.checkbox("Show unusable clips", value=False)
    filter_category = st.selectbox(
        "Category", ["All", "travel", "b-roll", "portrait", "action",
                     "golden-hour", "food", "architecture", "nature", "other"]
    )
    filter_mood = st.selectbox(
        "Mood", ["All", "cinematic", "energetic", "peaceful", "dramatic",
                 "intimate", "adventurous", "romantic", "other"]
    )
    proposal_count = st.slider("Proposals to generate", 3, 10, 5)

# ── Scan logic ────────────────────────────────────────────────────────────────
if scan_btn and folder_input:
    folder = folder_input.strip()
    if not Path(folder).is_dir():
        st.error(f"Not a valid directory: {folder}")
    else:
        videos = extractor.find_videos(folder)
        if not videos:
            st.warning("No video files found.")
        else:
            st.info(f"Found {len(videos)} video(s). Analyzing new clips...")
            progress = st.progress(0)
            status_text = st.empty()

            for i, vpath in enumerate(videos):
                progress.progress((i + 1) / len(videos))
                status_text.text(f"Processing {vpath.name}...")

                md5 = cache.compute_md5(str(vpath))
                existing = cache.get_clip_by_hash(md5, DB_PATH)

                if existing and existing.get("analyzed_at"):
                    continue  # already analyzed

                meta = extractor.get_video_metadata(str(vpath))

                # register clip as pending
                cache.upsert_clip({
                    "filepath": str(vpath),
                    "filename": vpath.name,
                    "md5_hash": md5,
                    **meta,
                    "analyzed_at": None,
                }, db_path=DB_PATH)

                # extract frames and analyze
                frame_paths = extractor.extract_frames(str(vpath), max_frames=6)
                if frame_paths:
                    analysis = claude_client.analyze_frames(frame_paths)
                    extractor.cleanup_frames(frame_paths)
                else:
                    analysis = {}

                # extract thumbnail
                extractor.extract_thumbnail(str(vpath), THUMBS_DIR, md5)

                from datetime import datetime
                if analysis and "error" not in analysis:
                    cache.upsert_clip({
                        "filepath": str(vpath),
                        "filename": vpath.name,
                        "md5_hash": md5,
                        **meta,
                        "analyzed_at": datetime.now().isoformat(),
                        "quality_score": analysis.get("quality_score"),
                        "scene_type": analysis.get("scene_type"),
                        "mood": analysis.get("mood"),
                        "lighting": analysis.get("lighting"),
                        "activities": json.dumps(analysis.get("activities", [])),
                        "category": analysis.get("category"),
                        "is_usable": 1 if analysis.get("is_usable", True) else 0,
                        "notes": analysis.get("notes"),
                        "raw_analysis": json.dumps(analysis),
                    }, db_path=DB_PATH)
                else:
                    cache.upsert_clip({
                        "filepath": str(vpath),
                        "filename": vpath.name,
                        "md5_hash": md5,
                        **meta,
                        "analyzed_at": datetime.now().isoformat(),
                        "is_usable": 0,
                        "notes": analysis.get("error", "analysis failed"),
                    }, db_path=DB_PATH)

            progress.progress(1.0)
            status_text.text("Done.")
            st.success("Scan complete.")

# ── Proposal generation ───────────────────────────────────────────────────────
if proposal_btn:
    with st.spinner("Generating proposals with Claude..."):
        results = proposals.run_proposal_generation(count=proposal_count, db_path=DB_PATH)
    if results:
        st.success(f"{len(results)} proposals generated.")
    else:
        st.warning("No proposals returned. Make sure you have analyzed clips first.")

# ── Main tabs ─────────────────────────────────────────────────────────────────
tab_library, tab_proposals, tab_trends = st.tabs(["Clip Library", "Proposals", "Trends"])

# ── Tab 1: Clip Library ───────────────────────────────────────────────────────
with tab_library:
    all_clips = cache.get_all_clips(usable_only=False, db_path=DB_PATH)

    # apply filters
    filtered = []
    for c in all_clips:
        if not show_unusable and not c.get("is_usable", 1):
            continue
        if filter_category != "All" and c.get("category") != filter_category:
            continue
        if filter_mood != "All" and c.get("mood") != filter_mood:
            continue
        filtered.append(c)

    if not filtered:
        st.info("No clips yet. Enter a folder path and click Scan.")
    else:
        st.caption(f"{len(filtered)} clip(s)")

        cols = st.columns(3)
        for i, clip in enumerate(filtered):
            with cols[i % 3]:
                # thumbnail
                thumb = THUMBS_DIR / f"{clip['md5_hash']}.jpg"
                if thumb.exists():
                    st.image(str(thumb), use_container_width=True)
                else:
                    st.markdown("🎥")

                # quality badge color
                qs = clip.get("quality_score") or 0
                if qs >= 7:
                    badge = f"🟢 {qs}/10"
                elif qs >= 5:
                    badge = f"🟡 {qs}/10"
                else:
                    badge = f"🔴 {qs}/10" if qs else "⬜ unscored"

                usable_flag = "" if clip.get("is_usable", 1) else " ⚠️"
                dur = f"{clip.get('duration_seconds', 0):.1f}s"

                st.markdown(f"**{clip['filename']}**{usable_flag}")
                st.caption(f"{badge} · {dur} · {clip.get('resolution', '')}")

                tags = " ".join(filter(None, [
                    clip.get("category"), clip.get("mood"), clip.get("lighting")
                ]))
                if tags:
                    st.caption(f"`{tags}`")

                with st.expander("Details"):
                    if clip.get("notes"):
                        st.write(clip["notes"])
                    if clip.get("scene_type"):
                        st.write(f"**Scene:** {clip['scene_type']}")
                    activities = json.loads(clip.get("activities") or "[]")
                    if activities:
                        st.write(f"**Activities:** {', '.join(activities)}")
                    st.caption(f"File: {clip['filepath']}")

# ── Tab 2: Proposals ──────────────────────────────────────────────────────────
with tab_proposals:
    all_proposals = cache.get_proposals(db_path=DB_PATH)

    if not all_proposals:
        st.info("No proposals yet. Click Propose in the sidebar after scanning clips.")
    else:
        platform_filter = st.selectbox(
            "Filter by platform",
            ["All", "tiktok", "reels", "youtube-shorts", "youtube"],
            key="platform_filter"
        )

        clips_by_id = {c["id"]: c for c in cache.get_all_clips(db_path=DB_PATH)}

        for p in all_proposals:
            if platform_filter != "All" and p.get("platform") != platform_filter:
                continue

            platform_emoji = {
                "tiktok": "🎵", "reels": "📱",
                "youtube-shorts": "▶️", "youtube": "🎬"
            }.get(p.get("platform", ""), "🎥")

            with st.container(border=True):
                col_left, col_right = st.columns([3, 1])

                with col_left:
                    st.markdown(
                        f"### {platform_emoji} {p.get('title', 'Untitled')}  "
                        f"`{p.get('platform', '')}` · {p.get('estimated_duration', '')} · "
                        f"_{p.get('vibe', '')}_"
                    )

                    # clips used
                    clip_ids = json.loads(p.get("clip_ids") or "[]")
                    clip_names = [
                        clips_by_id[cid]["filename"]
                        for cid in clip_ids
                        if cid in clips_by_id
                    ]
                    if clip_names:
                        st.caption("Clips: " + " → ".join(clip_names))

                    if p.get("hook_text"):
                        st.markdown(f"**Hook:** {p['hook_text']}")

                    with st.expander("Caption & Hashtags"):
                        st.write(p.get("caption", ""))
                        hashtags = json.loads(p.get("hashtags") or "[]")
                        if hashtags:
                            st.write(" ".join(f"#{h}" for h in hashtags))

                with col_right:
                    status = p.get("status", "saved")
                    st.caption(f"Status: **{status}**")
                    pid = p["id"]
                    if st.button("✅ Done", key=f"done_{pid}"):
                        cache.update_proposal_status(pid, "done", DB_PATH)
                        st.rerun()
                    if st.button("🗑 Dismiss", key=f"dismiss_{pid}"):
                        cache.update_proposal_status(pid, "dismissed", DB_PATH)
                        st.rerun()

# ── Tab 3: Trends ─────────────────────────────────────────────────────────────
with tab_trends:
    age = cache.get_trends_age(DB_PATH)
    col_h, col_btn = st.columns([3, 1])
    with col_h:
        if age:
            st.caption(f"Trend data cached · last updated {age}")
        else:
            st.caption("No trend data yet. Click Fetch to pull current trends.")
    with col_btn:
        fetch_btn = st.button("Fetch Trends", type="primary")

    if fetch_btn:
        with st.spinner("Searching for current trends with Claude..."):
            fetched = research.fetch_trends(db_path=DB_PATH)
        if fetched:
            st.success(f"Fetched {len(fetched)} trends.")
            st.rerun()
        else:
            st.warning("No trends returned. Try again or check Claude CLI.")

    trend_data = cache.get_cached_trends(max_age_hours=48, db_path=DB_PATH) or []

    if not trend_data:
        st.info("Click Fetch Trends to pull current social media trends.")
    else:
        CATEGORY_ORDER = ["format", "sound", "effect", "hook"]
        CATEGORY_LABELS = {
            "format": "🎬 Video Formats",
            "sound": "🎵 Trending Sounds",
            "effect": "✨ Effects & Editing",
            "hook": "🪝 Hook Styles",
        }
        PLATFORM_EMOJI = {"tiktok": "TT", "reels": "IG", "youtube-shorts": "YT", "all": "ALL"}

        filter_platform = st.selectbox(
            "Filter by platform",
            ["All", "tiktok", "reels", "youtube-shorts"],
            key="trend_platform"
        )
        filter_cat = st.selectbox(
            "Filter by category",
            ["All"] + CATEGORY_ORDER,
            key="trend_cat"
        )

        for cat in CATEGORY_ORDER:
            if filter_cat != "All" and filter_cat != cat:
                continue

            cat_trends = [
                t for t in trend_data
                if t.get("category") == cat
                and (filter_platform == "All" or t.get("platform") in (filter_platform, "all"))
            ]
            if not cat_trends:
                continue

            st.markdown(f"### {CATEGORY_LABELS.get(cat, cat)}")
            cols = st.columns(2)
            for i, t in enumerate(cat_trends):
                with cols[i % 2]:
                    pop = t.get("popularity", "")
                    pop_badge = {"trending": "🔥", "rising": "↑", "established": "●"}.get(pop, "")
                    plat = PLATFORM_EMOJI.get(t.get("platform", "all"), "ALL")
                    with st.container(border=True):
                        st.markdown(f"**{t.get('name', '')}** `{plat}` {pop_badge}")
                        st.caption(t.get("description", ""))
                        if t.get("relevance_to_travel"):
                            st.markdown(f"*→ {t['relevance_to_travel']}*")
                        if t.get("view_indicator"):
                            st.caption(f"📊 {t['view_indicator']}")
