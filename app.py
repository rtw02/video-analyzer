import streamlit as st
import json
import base64
from pathlib import Path
from datetime import datetime

from analyzer import cache, extractor, claude_client, proposals, research

DB_PATH = Path(__file__).parent / "data" / "clips.db"
THUMBS_DIR = Path(__file__).parent / "data" / "thumbs"

cache.init_db(DB_PATH)

st.set_page_config(page_title="Footage", layout="wide", page_icon="🎬",
                   initial_sidebar_state="expanded")

# ── Global CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=Epilogue:wght@300;400;500;600&display=swap');

#MainMenu, footer, header, .stDeployButton { display: none !important; }

:root {
  --bg:        #0C0D10;
  --bg-alt:    #101115;
  --surface:   #14151B;
  --surface-2: #1D1F28;
  --surface-3: #272A36;
  --border:    #22242E;
  --border-s:  #191B23;
  --accent:    #C9A74A;
  --text:      #E8E5DF;
  --text-2:    #A8ABBA;
  --text-3:    #5C606E;
  --good:      #5DBB8A;
  --warn:      #D98C3E;
  --bad:       #CC4E4E;
}

.stApp { background: var(--bg) !important; color: var(--text); font-family: 'Epilogue', system-ui, sans-serif; }
.main .block-container { padding-top: 1.2rem; padding-left: 1.5rem; padding-right: 1.5rem; max-width: 100%; }

[data-testid="stSidebar"] { background: var(--bg-alt) !important; border-right: 1px solid var(--border) !important; }
[data-testid="stSidebar"] > div { background: var(--bg-alt) !important; }
[data-testid="stSidebar"] label, [data-testid="stSidebar"] p, [data-testid="stSidebar"] span,
[data-testid="stSidebar"] .stMarkdown p { color: var(--text-2) !important; font-family: 'Epilogue', sans-serif !important; font-size: 12px !important; }

.stButton > button {
  background: transparent !important; border: 1px solid var(--border) !important;
  color: var(--text-2) !important; font-family: 'Epilogue', sans-serif !important;
  font-size: 12px !important; font-weight: 500 !important; border-radius: 4px !important;
  padding: 6px 14px !important; transition: all .1s !important;
}
.stButton > button:hover { background: var(--surface-2) !important; color: var(--text) !important; }
.stButton > button[kind="primary"] {
  background: var(--accent) !important; border-color: var(--accent) !important; color: #0C0D10 !important; font-weight: 600 !important;
}
.stButton > button[kind="primary"]:hover { filter: brightness(1.08) !important; }

.stTextInput > div > div > input {
  background: var(--surface) !important; border: 1px solid var(--border) !important;
  color: var(--text) !important; font-family: 'Epilogue', sans-serif !important; border-radius: 4px !important;
}
.stTextInput > div > div > input:focus { border-color: rgba(201,167,74,.4) !important; box-shadow: none !important; }
.stTextInput label { color: var(--text-3) !important; font-size: 10px !important; font-family: 'Epilogue', sans-serif !important; text-transform: uppercase; letter-spacing: .04em; }

.stSelectbox > div > div { background: var(--surface) !important; border: 1px solid var(--border) !important; color: var(--text) !important; }
.stSelectbox label { color: var(--text-3) !important; font-size: 10px !important; font-family: 'Epilogue', sans-serif !important; text-transform: uppercase; letter-spacing: .04em; }
.stCheckbox label { color: var(--text-2) !important; font-family: 'Epilogue', sans-serif !important; font-size: 12px !important; }

.stTabs [data-baseweb="tab-list"] { background: transparent !important; border-bottom: 1px solid var(--border) !important; gap: 0 !important; }
.stTabs [data-baseweb="tab"] {
  background: transparent !important; color: var(--text-3) !important;
  font-family: 'Epilogue', sans-serif !important; font-size: 12px !important; font-weight: 400 !important;
  padding: 8px 18px !important; border-bottom: 2px solid transparent !important;
}
.stTabs [data-baseweb="tab"][aria-selected="true"] { color: var(--text) !important; border-bottom: 2px solid var(--accent) !important; }
.stTabs [data-baseweb="tab-panel"] { padding-top: 1rem !important; background: transparent !important; }

.stProgress > div > div > div { background: var(--accent) !important; }
.stAlert { background: var(--surface) !important; border: 1px solid var(--border) !important; color: var(--text-2) !important; font-family: 'Epilogue', sans-serif !important; font-size: 12px !important; }
.stCaption p, .stCaption { color: var(--text-3) !important; font-family: 'Epilogue', sans-serif !important; font-size: 10px !important; }
hr { border-color: var(--border) !important; }
code { background: var(--surface-2) !important; color: var(--text-2) !important; border-radius: 2px !important; }

.stExpander { border: 1px solid var(--border-s) !important; border-radius: 4px !important; background: var(--surface) !important; }
.stExpander summary { color: var(--text-3) !important; font-family: 'Epilogue', sans-serif !important; font-size: 10px !important; }

[data-testid="stContainer"] { background: transparent !important; }

::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--surface-3); border-radius: 2px; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────
def quality_color(qs):
    if not qs:
        return '#5C606E'
    return '#5DBB8A' if qs >= 7 else '#D98C3E' if qs >= 5 else '#CC4E4E'

def thumb_b64(md5):
    p = THUMBS_DIR / f"{md5}.jpg"
    if p.exists():
        return base64.b64encode(p.read_bytes()).decode()
    return None

def clip_card_html(clip):
    qs = clip.get('quality_score') or 0
    qc = quality_color(qs)
    dur = f"{clip.get('duration_seconds', 0):.1f}s"
    res = clip.get('resolution', '')
    cat = clip.get('category', '')
    mood = clip.get('mood', '')
    usable = clip.get('is_usable', 1)
    opacity = '1' if usable else '0.48'

    b64 = thumb_b64(clip.get('md5_hash', ''))
    if b64:
        img = f'<img src="data:image/jpeg;base64,{b64}" style="width:100%;display:block;aspect-ratio:16/9;object-fit:cover;">'
    else:
        img = '<div style="width:100%;aspect-ratio:16/9;background:#1D1F28;display:flex;align-items:center;justify-content:center;color:#5C606E;font-size:11px;">no thumb</div>'

    tags = ''
    if cat:
        tags += f'<span style="font-size:9px;padding:1px 5px;background:rgba(88,166,255,.1);color:#58A6FF;border-radius:2px;">{cat}</span>'
    if not usable:
        tags += '<span style="font-size:9px;padding:1px 5px;background:rgba(204,78,78,.1);color:#CC4E4E;border-radius:2px;">unusable</span>'

    qs_label = f'{qs}/10' if qs else '–'

    return f"""
<div style="background:#14151B;border:1px solid #191B23;opacity:{opacity};margin-bottom:6px;position:relative;">
  <div style="height:3px;width:100%;background:{qc};"></div>
  <div style="position:relative;">
    {img}
    <span style="position:absolute;bottom:4px;right:4px;font-size:9px;background:rgba(0,0,0,.65);color:rgba(255,255,255,.88);padding:1px 4px;border-radius:2px;font-variant-numeric:tabular-nums;">{dur}</span>
    <span style="position:absolute;bottom:4px;left:4px;font-size:9px;background:rgba(0,0,0,.65);color:{qc};padding:1px 4px;border-radius:2px;font-variant-numeric:tabular-nums;">{qs_label}</span>
  </div>
  <div style="padding:6px 8px 7px;">
    <div style="font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#E8E5DF;margin-bottom:3px;">{clip['filename']}</div>
    <div style="font-size:9px;color:#5C606E;margin-bottom:4px;">{res}</div>
    <div style="display:flex;gap:3px;flex-wrap:wrap;">{tags}</div>
  </div>
</div>"""

def cat_header_html(name, count):
    return f"""
<div style="display:flex;align-items:baseline;gap:12px;margin:24px 0 12px 0;">
  <span style="font-family:'Cormorant Garamond',serif;font-style:italic;font-size:22px;font-weight:400;color:#E8E5DF;">{name}</span>
  <span style="font-family:'Epilogue',sans-serif;font-size:10px;color:#5C606E;">{count} clip{'s' if count!=1 else ''}</span>
  <div style="flex:1;height:1px;background:#191B23;position:relative;top:-3px;"></div>
</div>"""

def proposal_card_html(p, clip_names):
    plat = p.get('platform', '')
    plat_colors = {
        'tiktok':        ('rgba(0,212,206,.1)',  '#00D4CE', 'TIKTOK'),
        'reels':         ('rgba(194,100,184,.1)', '#C264B8', 'REELS'),
        'youtube-shorts':('rgba(224,51,51,.08)',  '#E03333', 'SHORTS'),
        'youtube':       ('rgba(224,51,51,.1)',   '#E03333', 'YOUTUBE'),
    }
    bg, fg, label = plat_colors.get(plat, ('rgba(100,100,100,.1)', '#888', plat.upper()))
    clips_html = ' '.join(
        f'<span style="font-size:9px;padding:2px 6px;background:#1D1F28;border:1px solid #22242E;border-radius:2px;color:#5C606E;">{n}</span>'
        for n in clip_names
    )
    hook = p.get('hook_text','')
    return f"""
<div style="background:#14151B;border:1px solid #191B23;padding:14px 16px;margin-bottom:10px;">
  <div style="display:flex;align-items:flex-start;gap:8px;margin-bottom:8px;">
    <span style="font-size:9px;font-weight:600;letter-spacing:.04em;padding:2px 6px;border-radius:2px;background:{bg};color:{fg};margin-top:2px;white-space:nowrap;">{label}</span>
    <div style="font-family:'Cormorant Garamond',serif;font-size:16px;font-weight:500;line-height:1.25;color:#E8E5DF;">{p.get('title','')}</div>
  </div>
  <div style="font-size:10px;color:#5C606E;margin-bottom:6px;">{p.get('estimated_duration','')} · {p.get('vibe','')}</div>
  <div style="font-size:11px;color:#A8ABBA;border-left:2px solid #C9A74A;padding-left:8px;margin-bottom:9px;line-height:1.45;font-style:italic;">{hook}</div>
  <div style="display:flex;gap:4px;flex-wrap:wrap;">{clips_html}</div>
</div>"""


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
<div style="padding:4px 0 16px 0;border-bottom:1px solid #22242E;margin-bottom:16px;">
  <span style="font-family:'Cormorant Garamond',serif;font-size:24px;font-weight:500;color:#E8E5DF;letter-spacing:.01em;">
    <em style="color:#C9A74A;font-style:italic;">F</em>ootage
  </span>
</div>""", unsafe_allow_html=True)

    folder_input = st.text_input("Folder path", placeholder="/Users/you/Videos/trip")
    col1, col2 = st.columns(2)
    scan_btn     = col1.button("Scan",    use_container_width=True, type="primary")
    proposal_btn = col2.button("Propose", use_container_width=True)

    st.divider()
    show_unusable   = st.checkbox("Show unusable clips", value=False)
    filter_category = st.selectbox("Category", ["All","travel","b-roll","portrait","action",
                                                 "golden-hour","food","architecture","nature","other"])
    filter_mood     = st.selectbox("Mood", ["All","cinematic","energetic","peaceful","dramatic",
                                            "intimate","adventurous","romantic","other"])
    proposal_count  = st.slider("Proposals to generate", 3, 10, 5)

    st.divider()
    st.markdown('<div style="font-size:10px;color:#5C606E;text-transform:uppercase;letter-spacing:.04em;margin-bottom:8px;">Custom Categories</div>', unsafe_allow_html=True)
    custom_cats = cache.get_custom_categories(DB_PATH)
    for cc in custom_cats:
        col_cc, col_del = st.columns([3,1])
        col_cc.markdown(f'<div style="font-size:11px;color:#A8ABBA;padding:2px 0;">{cc["name"]}</div>', unsafe_allow_html=True)
        if col_del.button("✕", key=f"del_cat_{cc['id']}", help="Delete category"):
            cache.delete_custom_category(cc["name"], DB_PATH)
            st.rerun()
    with st.form("new_cat_form", clear_on_submit=True):
        new_cat_name = st.text_input("New category name", placeholder="e.g. transitions", label_visibility="collapsed")
        if st.form_submit_button("Add", use_container_width=True):
            if new_cat_name.strip():
                cache.add_custom_category(new_cat_name.strip(), db_path=DB_PATH)
                st.rerun()

    # quality summary
    all_clips_sidebar = cache.get_all_clips(usable_only=False, db_path=DB_PATH)
    if all_clips_sidebar:
        hi  = sum(1 for c in all_clips_sidebar if (c.get('quality_score') or 0) >= 7)
        mid = sum(1 for c in all_clips_sidebar if 5 <= (c.get('quality_score') or 0) < 7)
        lo  = sum(1 for c in all_clips_sidebar if 0 < (c.get('quality_score') or 0) < 5)
        total = len(all_clips_sidebar)
        def pct(n): return f"{round(n/total*100)}%" if total else "0%"
        st.markdown(f"""
<div style="background:#14151B;border:1px solid #22242E;border-radius:4px;padding:10px 12px;margin-top:12px;">
  <div style="font-size:10px;color:#5C606E;margin-bottom:8px;">Quality distribution</div>
  <div style="display:flex;align-items:center;gap:6px;margin-bottom:5px;">
    <span style="font-size:10px;color:#5DBB8A;width:28px;text-align:right;">High</span>
    <div style="flex:1;height:3px;background:#272A36;border-radius:1px;">
      <div style="width:{pct(hi)};height:100%;background:#5DBB8A;border-radius:1px;"></div>
    </div>
    <span style="font-size:10px;color:#5C606E;">{hi}</span>
  </div>
  <div style="display:flex;align-items:center;gap:6px;margin-bottom:5px;">
    <span style="font-size:10px;color:#D98C3E;width:28px;text-align:right;">Mid</span>
    <div style="flex:1;height:3px;background:#272A36;border-radius:1px;">
      <div style="width:{pct(mid)};height:100%;background:#D98C3E;border-radius:1px;"></div>
    </div>
    <span style="font-size:10px;color:#5C606E;">{mid}</span>
  </div>
  <div style="display:flex;align-items:center;gap:6px;">
    <span style="font-size:10px;color:#CC4E4E;width:28px;text-align:right;">Low</span>
    <div style="flex:1;height:3px;background:#272A36;border-radius:1px;">
      <div style="width:{pct(lo)};height:100%;background:#CC4E4E;border-radius:1px;"></div>
    </div>
    <span style="font-size:10px;color:#5C606E;">{lo}</span>
  </div>
</div>""", unsafe_allow_html=True)


# ── Scan ──────────────────────────────────────────────────────────────────────
if scan_btn and folder_input:
    folder = folder_input.strip()
    if not Path(folder).is_dir():
        st.error(f"Not a valid directory: {folder}")
    else:
        videos = extractor.find_videos(folder)
        if not videos:
            st.warning("No video files found.")
        else:
            st.info(f"Found {len(videos)} video(s). Analyzing new clips…")
            progress    = st.progress(0)
            status_text = st.empty()

            for i, vpath in enumerate(videos):
                progress.progress((i + 1) / len(videos))
                status_text.text(f"Processing {vpath.name}…")

                md5     = cache.compute_md5(str(vpath))
                existing = cache.get_clip_by_hash(md5, DB_PATH)
                if existing and existing.get("analyzed_at"):
                    continue

                meta = extractor.get_video_metadata(str(vpath))
                cache.upsert_clip({"filepath": str(vpath), "filename": vpath.name,
                                   "md5_hash": md5, **meta, "analyzed_at": None}, db_path=DB_PATH)

                custom_cat_names = [c["name"] for c in cache.get_custom_categories(DB_PATH)]
                frame_paths = extractor.extract_frames(str(vpath), max_frames=6)
                analysis = claude_client.analyze_frames(frame_paths, custom_categories=custom_cat_names or None) if frame_paths else {}
                extractor.cleanup_frames(frame_paths)
                extractor.extract_thumbnail(str(vpath), THUMBS_DIR, md5)

                now = datetime.now().isoformat()
                if analysis and "error" not in analysis:
                    cache.upsert_clip({"filepath": str(vpath), "filename": vpath.name,
                                       "md5_hash": md5, **meta, "analyzed_at": now,
                                       "quality_score":  analysis.get("quality_score"),
                                       "scene_type":     analysis.get("scene_type"),
                                       "mood":           analysis.get("mood"),
                                       "lighting":       analysis.get("lighting"),
                                       "activities":     json.dumps(analysis.get("activities", [])),
                                       "category":       analysis.get("category"),
                                       "is_usable":      1 if analysis.get("is_usable", True) else 0,
                                       "notes":          analysis.get("notes"),
                                       "raw_analysis":   json.dumps(analysis)}, db_path=DB_PATH)
                else:
                    cache.upsert_clip({"filepath": str(vpath), "filename": vpath.name,
                                       "md5_hash": md5, **meta, "analyzed_at": now,
                                       "is_usable": 0, "notes": analysis.get("error", "analysis failed")}, db_path=DB_PATH)

            progress.progress(1.0)
            status_text.text("Done.")
            st.success("Scan complete.")


# ── Propose ───────────────────────────────────────────────────────────────────
if proposal_btn:
    with st.spinner("Generating proposals with Claude…"):
        results = proposals.run_proposal_generation(count=proposal_count, db_path=DB_PATH)
    if results:
        st.success(f"{len(results)} proposals generated.")
    else:
        st.warning("No proposals returned. Scan and analyze clips first.")


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_library, tab_proposals, tab_groups, tab_trends = st.tabs(["Library", "Proposals", "Groups", "Trends"])


# ── Library ───────────────────────────────────────────────────────────────────
with tab_library:
    all_clips = cache.get_all_clips(usable_only=False, db_path=DB_PATH)
    filtered = [
        c for c in all_clips
        if (show_unusable or c.get("is_usable", 1))
        and (filter_category == "All" or c.get("category") == filter_category)
        and (filter_mood == "All" or c.get("mood") == filter_mood)
    ]

    if not filtered:
        st.markdown('<div style="color:#5C606E;font-size:12px;padding:24px 0;">No clips yet. Enter a folder path and click Scan.</div>', unsafe_allow_html=True)
    else:
        usable_count = sum(1 for c in filtered if c.get('is_usable', 1))
        st.markdown(f'<div style="font-size:10px;color:#5C606E;margin-bottom:4px;">{len(filtered)} clips · {usable_count} usable</div>', unsafe_allow_html=True)

        # group by category
        cat_order = ["travel","golden-hour","action","nature","b-roll","portrait","food","architecture","other"]
        by_cat = {}
        for c in filtered:
            cat = c.get("category") or "other"
            by_cat.setdefault(cat, []).append(c)

        for cat in cat_order + [k for k in by_cat if k not in cat_order]:
            clips_in_cat = by_cat.get(cat)
            if not clips_in_cat:
                continue
            if filter_category == "All":
                st.markdown(cat_header_html(cat.replace("-"," ").title(), len(clips_in_cat)), unsafe_allow_html=True)

            cols = st.columns(4)
            for i, clip in enumerate(clips_in_cat):
                with cols[i % 4]:
                    st.markdown(clip_card_html(clip), unsafe_allow_html=True)
                    with st.expander("Details"):
                        if clip.get("notes"):
                            st.write(clip["notes"])
                        rows = []
                        if clip.get("scene_type"):  rows.append(f"**Scene** {clip['scene_type']}")
                        if clip.get("mood"):         rows.append(f"**Mood** {clip['mood']}")
                        if clip.get("lighting"):     rows.append(f"**Light** {clip['lighting']}")
                        acts = json.loads(clip.get("activities") or "[]")
                        if acts: rows.append(f"**Activities** {', '.join(acts)}")
                        for r in rows:
                            st.markdown(f'<div style="font-size:11px;color:#A8ABBA;margin-bottom:2px;">{r}</div>', unsafe_allow_html=True)
                        st.caption(clip['filepath'])

                        # reassign category
                        custom_cats_lib = cache.get_custom_categories(DB_PATH)
                        all_cats = cache.BASE_CATEGORIES + [c["name"] for c in custom_cats_lib]
                        current_cat = clip.get("category") or "other"
                        new_cat = st.selectbox("Category", all_cats,
                                               index=all_cats.index(current_cat) if current_cat in all_cats else 0,
                                               key=f"cat_{clip['id']}")
                        if new_cat != current_cat:
                            if st.button("Apply", key=f"apply_cat_{clip['id']}"):
                                cache.set_clip_category(clip["id"], new_cat, DB_PATH)
                                st.rerun()

                        # add to group
                        groups_lib = cache.get_groups(DB_PATH)
                        if groups_lib:
                            group_names = [f"{g['name']} ({g['clip_count']} clips)" for g in groups_lib]
                            chosen_g = st.selectbox("Add to group", ["—"] + group_names, key=f"grp_{clip['id']}")
                            if chosen_g != "—":
                                idx = group_names.index(chosen_g)
                                if st.button("Add", key=f"add_grp_{clip['id']}"):
                                    cache.add_clip_to_group(groups_lib[idx]["id"], clip["id"], DB_PATH)
                                    st.success("Added.")


# ── Proposals ─────────────────────────────────────────────────────────────────
with tab_proposals:
    all_proposals = cache.get_proposals(db_path=DB_PATH)

    if not all_proposals:
        st.markdown('<div style="color:#5C606E;font-size:12px;padding:24px 0;">No proposals yet. Click Propose after scanning clips.</div>', unsafe_allow_html=True)
    else:
        platform_filter = st.selectbox("Platform", ["All","tiktok","reels","youtube-shorts","youtube"],
                                       key="platform_filter")
        clips_by_id = {c["id"]: c for c in cache.get_all_clips(db_path=DB_PATH)}

        for p in all_proposals:
            if platform_filter != "All" and p.get("platform") != platform_filter:
                continue
            clip_ids   = json.loads(p.get("clip_ids") or "[]")
            clip_names = [clips_by_id[cid]["filename"] for cid in clip_ids if cid in clips_by_id]

            st.markdown(proposal_card_html(p, clip_names), unsafe_allow_html=True)

            col_caption, col_done, col_dismiss = st.columns([2,1,1])
            with col_caption:
                with st.expander("Caption & hashtags"):
                    st.write(p.get("caption",""))
                    tags = json.loads(p.get("hashtags") or "[]")
                    if tags:
                        st.write(" ".join(f"#{h}" for h in tags))
            with col_done:
                if st.button("Done", key=f"done_{p['id']}"):
                    cache.update_proposal_status(p['id'], "done", DB_PATH)
                    st.rerun()
            with col_dismiss:
                if st.button("Dismiss", key=f"dismiss_{p['id']}"):
                    cache.update_proposal_status(p['id'], "dismissed", DB_PATH)
                    st.rerun()

            st.markdown('<div style="height:1px;background:#191B23;margin:4px 0 12px 0;"></div>', unsafe_allow_html=True)


# ── Groups ────────────────────────────────────────────────────────────────────
with tab_groups:
    all_groups = cache.get_groups(DB_PATH)

    col_g1, col_g2 = st.columns([3, 1])
    with col_g1:
        st.markdown('<div style="font-family:\'Cormorant Garamond\',serif;font-style:italic;font-size:22px;font-weight:400;color:#E8E5DF;margin-bottom:12px;">Sequences & Groups</div>', unsafe_allow_html=True)
    with col_g2:
        if st.button("New Group", type="primary"):
            st.session_state["creating_group"] = True

    # new group form
    if st.session_state.get("creating_group"):
        with st.form("new_group_form"):
            g_name = st.text_input("Group name", placeholder="e.g. Golden Hour Arc")
            g_type = st.selectbox("Type", [
                "custom", "transition-sequence", "narrative-arc",
                "b-roll-pack", "timeline", "mood-reel", "opening-sequence"
            ])
            g_desc = st.text_input("Description (optional)", placeholder="What is this group for?")
            col_s, col_c = st.columns(2)
            submitted = col_s.form_submit_button("Create", use_container_width=True)
            cancelled = col_c.form_submit_button("Cancel", use_container_width=True)
            if submitted and g_name.strip():
                cache.create_group(g_name.strip(), g_type, g_desc, DB_PATH)
                st.session_state["creating_group"] = False
                st.rerun()
            if cancelled:
                st.session_state["creating_group"] = False
                st.rerun()

    if not all_groups:
        st.markdown('<div style="color:#5C606E;font-size:12px;padding:16px 0;">No groups yet. Create a group, then add clips from the Library tab.</div>', unsafe_allow_html=True)
    else:
        # check if a group is open
        open_id = st.session_state.get("open_group")

        if open_id:
            group = cache.get_group(open_id, DB_PATH)
            if not group:
                st.session_state.pop("open_group")
                st.rerun()

            # header row
            col_back, col_title, col_del = st.columns([1, 5, 1])
            if col_back.button("← Back"):
                st.session_state.pop("open_group", None)
                st.rerun()
            col_title.markdown(f"""
<div>
  <span style="font-family:'Cormorant Garamond',serif;font-style:italic;font-size:20px;color:#E8E5DF;">{group['name']}</span>
  <span style="font-size:10px;color:#5C606E;margin-left:8px;">{group['group_type']}</span>
</div>""", unsafe_allow_html=True)
            if col_del.button("Delete", key="del_group"):
                cache.delete_group(open_id, DB_PATH)
                st.session_state.pop("open_group", None)
                st.rerun()

            if group.get("description"):
                st.caption(group["description"])

            clips_in_group = group.get("clips", [])

            if not clips_in_group:
                st.markdown('<div style="color:#5C606E;font-size:12px;padding:12px 0;">No clips yet. Add clips from the Library tab.</div>', unsafe_allow_html=True)
            else:
                # sequence analysis results
                if group.get("arc") or group.get("edit_notes"):
                    st.markdown(f"""
<div style="background:#14151B;border:1px solid #191B23;padding:12px 14px;margin-bottom:14px;">
  <div style="font-size:10px;color:#5C606E;margin-bottom:6px;text-transform:uppercase;letter-spacing:.04em;">Sequence Analysis</div>
  {f'<div style="font-size:12px;color:#C9A74A;font-style:italic;margin-bottom:6px;">{group["arc"]}</div>' if group.get("arc") else ""}
  {f'<div style="font-size:11px;color:#A8ABBA;line-height:1.5;">{group["edit_notes"]}</div>' if group.get("edit_notes") else ""}
</div>""", unsafe_allow_html=True)

                # clip list with order controls
                st.markdown('<div style="font-size:10px;color:#5C606E;margin-bottom:8px;text-transform:uppercase;letter-spacing:.04em;">Clips in sequence order</div>', unsafe_allow_html=True)
                clip_ids_ordered = [c["id"] for c in clips_in_group]

                for idx, clip in enumerate(clips_in_group):
                    qs = clip.get("quality_score") or 0
                    qc = quality_color(qs)
                    b64 = thumb_b64(clip.get("md5_hash", ""))
                    thumb_html = f'<img src="data:image/jpeg;base64,{b64}" style="width:60px;height:34px;object-fit:cover;display:block;flex-shrink:0;">' if b64 else '<div style="width:60px;height:34px;background:#1D1F28;flex-shrink:0;"></div>'
                    t_note = clip.get("transition_note", "")

                    col_num, col_img, col_info, col_up, col_dn, col_rm = st.columns([0.4, 1.2, 5, 0.6, 0.6, 0.8])
                    col_num.markdown(f'<div style="font-size:14px;color:#5C606E;font-family:\'Cormorant Garamond\',serif;font-style:italic;padding-top:8px;">{idx+1}</div>', unsafe_allow_html=True)
                    col_img.markdown(f'<div style="border-top:3px solid {qc};margin-top:6px;">{thumb_html}</div>', unsafe_allow_html=True)
                    col_info.markdown(f"""
<div style="padding:4px 0;">
  <div style="font-size:11px;color:#E8E5DF;">{clip['filename']}</div>
  <div style="font-size:10px;color:#5C606E;">{clip.get('mood','')} · {clip.get('lighting','').replace('_',' ')}</div>
  {f'<div style="font-size:10px;color:#C9A74A;font-style:italic;margin-top:2px;">→ {t_note}</div>' if t_note else ''}
</div>""", unsafe_allow_html=True)

                    if col_up.button("↑", key=f"up_{open_id}_{clip['id']}", disabled=(idx == 0)):
                        new_order = clip_ids_ordered[:]
                        new_order[idx], new_order[idx-1] = new_order[idx-1], new_order[idx]
                        cache.reorder_group(open_id, new_order, DB_PATH)
                        st.rerun()
                    if col_dn.button("↓", key=f"dn_{open_id}_{clip['id']}", disabled=(idx == len(clips_in_group)-1)):
                        new_order = clip_ids_ordered[:]
                        new_order[idx], new_order[idx+1] = new_order[idx+1], new_order[idx]
                        cache.reorder_group(open_id, new_order, DB_PATH)
                        st.rerun()
                    if col_rm.button("Remove", key=f"rm_{open_id}_{clip['id']}"):
                        cache.remove_clip_from_group(open_id, clip["id"], DB_PATH)
                        st.rerun()

                st.markdown('<div style="height:1px;background:#191B23;margin:12px 0;"></div>', unsafe_allow_html=True)

                # analyse sequence button
                if st.button("Analyze Sequence with Claude", type="primary"):
                    with st.spinner("Analyzing sequence…"):
                        result = claude_client.analyze_group_sequence(clips_in_group)
                    if "error" not in result:
                        # apply suggested order if valid
                        suggested = result.get("suggested_order", [])
                        valid_ids = set(clip_ids_ordered)
                        if suggested and all(i in valid_ids for i in suggested):
                            cache.reorder_group(open_id, suggested, DB_PATH)
                        cache.update_group_analysis(
                            open_id,
                            result.get("arc", ""),
                            f"{result.get('edit_notes','')}\nEstimated duration: {result.get('estimated_total_duration','')}",
                            result.get("transitions", []),
                            DB_PATH
                        )
                        st.success("Sequence analyzed. Order updated.")
                        st.rerun()
                    else:
                        st.error(f"Analysis failed: {result.get('error')}")

        else:
            # group list view
            GROUP_TYPE_COLORS = {
                "transition-sequence": "#58A6FF",
                "narrative-arc":       "#C9A74A",
                "b-roll-pack":         "#7A7D8A",
                "timeline":            "#5DBB8A",
                "mood-reel":           "#C264B8",
                "opening-sequence":    "#D98C3E",
                "custom":              "#5C606E",
            }
            cols = st.columns(3)
            for i, g in enumerate(all_groups):
                tc = GROUP_TYPE_COLORS.get(g["group_type"], "#5C606E")
                analyzed_badge = '<span style="font-size:9px;color:#5DBB8A;margin-left:6px;">✓ analyzed</span>' if g.get("analyzed_at") else ''
                with cols[i % 3]:
                    st.markdown(f"""
<div style="background:#14151B;border:1px solid #191B23;padding:12px 14px;margin-bottom:8px;">
  <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
    <span style="font-size:9px;padding:1px 6px;background:{tc}18;color:{tc};border-radius:2px;font-weight:600;">{g['group_type'].replace('-',' ').upper()}</span>
    {analyzed_badge}
  </div>
  <div style="font-family:'Cormorant Garamond',serif;font-size:16px;font-weight:500;color:#E8E5DF;margin-bottom:4px;">{g['name']}</div>
  <div style="font-size:10px;color:#5C606E;">{g['clip_count']} clip{'s' if g['clip_count']!=1 else ''}</div>
  {f'<div style="font-size:10px;color:#A8ABBA;margin-top:4px;font-style:italic;">{g["arc"][:80]}…</div>' if g.get("arc") else ''}
</div>""", unsafe_allow_html=True)
                    if st.button("Open", key=f"open_g_{g['id']}", use_container_width=True):
                        st.session_state["open_group"] = g["id"]
                        st.rerun()


# ── Trends ────────────────────────────────────────────────────────────────────
with tab_trends:
    age = cache.get_trends_age(DB_PATH)
    col_h, col_btn = st.columns([3,1])
    with col_h:
        st.caption(f"Cached · {age}" if age else "No trend data. Click Fetch.")
    with col_btn:
        fetch_btn = st.button("Fetch Trends", type="primary")

    if fetch_btn:
        with st.spinner("Searching for current trends with Claude…"):
            fetched = research.fetch_trends(db_path=DB_PATH)
        if fetched:
            st.success(f"Fetched {len(fetched)} trends.")
            st.rerun()
        else:
            st.warning("No trends returned. Try again or check Claude CLI.")

    trend_data = cache.get_cached_trends(max_age_hours=48, db_path=DB_PATH) or []

    if not trend_data:
        st.markdown('<div style="color:#5C606E;font-size:12px;padding:24px 0;">Click Fetch Trends to pull current social media trends.</div>', unsafe_allow_html=True)
    else:
        CATEGORY_ORDER  = ["format","sound","effect","hook"]
        CATEGORY_LABELS = {"format":"Formats","sound":"Sounds","effect":"Effects & Editing","hook":"Hook Styles"}
        PLATFORM_MAP    = {"tiktok":"TT","reels":"IG","youtube-shorts":"YT","all":"ALL"}
        POP_ICON        = {"trending":"🔥","rising":"↑","established":"●"}

        filter_platform = st.selectbox("Platform", ["All","tiktok","reels","youtube-shorts"], key="trend_plat")
        filter_cat_t    = st.selectbox("Category", ["All"] + CATEGORY_ORDER, key="trend_cat")

        for cat in CATEGORY_ORDER:
            if filter_cat_t != "All" and filter_cat_t != cat:
                continue
            cat_trends = [t for t in trend_data
                          if t.get("category") == cat
                          and (filter_platform == "All" or t.get("platform") in (filter_platform,"all"))]
            if not cat_trends:
                continue

            st.markdown(f'<div style="font-family:\'Cormorant Garamond\',serif;font-style:italic;font-size:18px;font-weight:400;color:#E8E5DF;margin:20px 0 10px 0;">{CATEGORY_LABELS.get(cat,cat)}</div>', unsafe_allow_html=True)
            cols = st.columns(2)
            for i, t in enumerate(cat_trends):
                pop  = t.get("popularity","")
                icon = POP_ICON.get(pop,"")
                plat = PLATFORM_MAP.get(t.get("platform","all"),"ALL")
                with cols[i % 2]:
                    st.markdown(f"""
<div style="background:#14151B;border:1px solid #191B23;padding:10px 12px;margin-bottom:6px;">
  <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
    <span style="font-size:8px;padding:1px 5px;background:#1D1F28;color:#5C606E;border-radius:2px;">{plat}</span>
    <span style="font-size:12px;font-weight:500;color:#E8E5DF;flex:1;">{t.get('name','')}</span>
    <span style="font-size:9px;color:#D98C3E;">{icon} {pop}</span>
  </div>
  <div style="font-size:11px;color:#A8ABBA;line-height:1.45;margin-bottom:4px;">{t.get('description','')}</div>
  {f'<div style="font-size:10px;color:#C9A74A;font-style:italic;">→ {t["relevance_to_travel"]}</div>' if t.get('relevance_to_travel') else ''}
  {f'<div style="font-size:9px;color:#5C606E;margin-top:3px;">{t["view_indicator"]}</div>' if t.get('view_indicator') else ''}
</div>""", unsafe_allow_html=True)
