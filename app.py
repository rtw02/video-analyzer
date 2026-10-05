import streamlit as st
import json
import base64
import csv
import io
from pathlib import Path
from datetime import datetime

from analyzer import cache, extractor, claude_client, proposals, research

DB_PATH = Path(__file__).parent / "data" / "clips.db"
THUMBS_DIR = Path(__file__).parent / "data" / "thumbs"

cache.init_db(DB_PATH)

st.set_page_config(page_title="Footage", layout="wide", page_icon="🎬",
                   initial_sidebar_state="expanded")

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=Epilogue:wght@300;400;500;600&display=swap');
#MainMenu,footer,header,.stDeployButton{display:none!important}
:root{
  --bg:#0C0D10;--bg-alt:#101115;--surface:#14151B;--surface-2:#1D1F28;--surface-3:#272A36;
  --border:#22242E;--border-s:#191B23;--accent:#C9A74A;
  --text:#E8E5DF;--text-2:#A8ABBA;--text-3:#5C606E;
  --good:#5DBB8A;--warn:#D98C3E;--bad:#CC4E4E;
}
.stApp{background:var(--bg)!important;color:var(--text);font-family:'Epilogue',system-ui,sans-serif}
.main .block-container{padding-top:1.2rem;padding-left:1.5rem;padding-right:1.5rem;max-width:100%}
[data-testid="stSidebar"]{background:var(--bg-alt)!important;border-right:1px solid var(--border)!important}
[data-testid="stSidebar"]>div{background:var(--bg-alt)!important}
[data-testid="stSidebar"] label,[data-testid="stSidebar"] p,[data-testid="stSidebar"] span,
[data-testid="stSidebar"] .stMarkdown p{color:var(--text-2)!important;font-family:'Epilogue',sans-serif!important;font-size:12px!important}
.stButton>button{background:transparent!important;border:1px solid var(--border)!important;color:var(--text-2)!important;font-family:'Epilogue',sans-serif!important;font-size:12px!important;font-weight:500!important;border-radius:4px!important;padding:6px 14px!important;transition:all .1s!important}
.stButton>button:hover{background:var(--surface-2)!important;color:var(--text)!important}
.stButton>button[kind="primary"]{background:var(--accent)!important;border-color:var(--accent)!important;color:#0C0D10!important;font-weight:600!important}
.stButton>button[kind="primary"]:hover{filter:brightness(1.08)!important}
.stTextInput>div>div>input{background:var(--surface)!important;border:1px solid var(--border)!important;color:var(--text)!important;font-family:'Epilogue',sans-serif!important;border-radius:4px!important}
.stTextInput>div>div>input:focus{border-color:rgba(201,167,74,.4)!important;box-shadow:none!important}
.stTextInput label{color:var(--text-3)!important;font-size:10px!important;font-family:'Epilogue',sans-serif!important;text-transform:uppercase;letter-spacing:.04em}
.stSelectbox>div>div{background:var(--surface)!important;border:1px solid var(--border)!important;color:var(--text)!important;font-family:'Epilogue',sans-serif!important}
.stSelectbox label{color:var(--text-3)!important;font-size:10px!important;font-family:'Epilogue',sans-serif!important;text-transform:uppercase;letter-spacing:.04em}
.stCheckbox label{color:var(--text-2)!important;font-family:'Epilogue',sans-serif!important;font-size:12px!important}
.stTabs [data-baseweb="tab-list"]{background:transparent!important;border-bottom:1px solid var(--border)!important;gap:0!important}
.stTabs [data-baseweb="tab"]{background:transparent!important;color:var(--text-3)!important;font-family:'Epilogue',sans-serif!important;font-size:12px!important;font-weight:400!important;padding:8px 18px!important;border-bottom:2px solid transparent!important}
.stTabs [data-baseweb="tab"][aria-selected="true"]{color:var(--text)!important;border-bottom:2px solid var(--accent)!important}
.stTabs [data-baseweb="tab-panel"]{padding-top:1rem!important;background:transparent!important}
.stProgress>div>div>div{background:var(--accent)!important}
.stAlert{background:var(--surface)!important;border:1px solid var(--border)!important;color:var(--text-2)!important;font-family:'Epilogue',sans-serif!important;font-size:12px!important}
.stCaption p,.stCaption{color:var(--text-3)!important;font-family:'Epilogue',sans-serif!important;font-size:10px!important}
hr{border-color:var(--border)!important}
code{background:var(--surface-2)!important;color:var(--text-2)!important;border-radius:2px!important}
.stExpander{border:1px solid var(--border-s)!important;border-radius:4px!important;background:var(--surface)!important}
.stExpander summary{color:var(--text-3)!important;font-family:'Epilogue',sans-serif!important;font-size:10px!important}
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--surface-3);border-radius:2px}
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────
def quality_color(qs):
    if not qs: return '#5C606E'
    return '#5DBB8A' if qs >= 7 else '#D98C3E' if qs >= 5 else '#CC4E4E'

def thumb_b64(md5):
    p = THUMBS_DIR / f"{md5}.jpg"
    return base64.b64encode(p.read_bytes()).decode() if p.exists() else None

def all_category_options():
    custom = [c["name"] for c in cache.get_custom_categories(DB_PATH)]
    return ["All"] + custom + [c for c in cache.BASE_CATEGORIES if c not in custom]

def clip_card_html(clip):
    qs  = clip.get('quality_score') or 0
    qc  = quality_color(qs)
    dur = f"{clip.get('duration_seconds', 0):.1f}s"
    res = clip.get('resolution', '')
    cat = clip.get('category', '')
    usable  = clip.get('is_usable', 1)
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
<div style="background:#14151B;border:1px solid #191B23;opacity:{opacity};margin-bottom:6px;">
  <div style="height:3px;width:100%;background:{qc};"></div>
  <div style="position:relative;">{img}
    <span style="position:absolute;bottom:4px;right:4px;font-size:9px;background:rgba(0,0,0,.65);color:rgba(255,255,255,.88);padding:1px 4px;border-radius:2px;">{dur}</span>
    <span style="position:absolute;bottom:4px;left:4px;font-size:9px;background:rgba(0,0,0,.65);color:{qc};padding:1px 4px;border-radius:2px;">{qs_label}</span>
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

def proposal_card_html(p, clip_mds5_list):
    plat = p.get('platform', '')
    plat_map = {
        'tiktok':         ('rgba(0,212,206,.1)',   '#00D4CE', 'TIKTOK'),
        'reels':          ('rgba(194,100,184,.1)',  '#C264B8', 'REELS'),
        'youtube-shorts': ('rgba(224,51,51,.08)',   '#E03333', 'SHORTS'),
        'youtube':        ('rgba(224,51,51,.1)',    '#E03333', 'YOUTUBE'),
    }
    bg, fg, label = plat_map.get(plat, ('rgba(100,100,100,.1)', '#888', plat.upper()))

    # storyboard strip
    sb = ''
    for md5 in clip_mds5_list[:4]:
        b64 = thumb_b64(md5)
        if b64:
            sb += f'<img src="data:image/jpeg;base64,{b64}" style="width:44px;height:25px;object-fit:cover;border-radius:1px;flex-shrink:0;">'
        else:
            sb += '<div style="width:44px;height:25px;background:#1D1F28;border-radius:1px;flex-shrink:0;"></div>'
    extra = len(clip_mds5_list) - 4
    if extra > 0:
        sb += f'<div style="width:28px;height:25px;display:flex;align-items:center;justify-content:center;font-size:9px;color:#5C606E;background:#1D1F28;border-radius:1px;">+{extra}</div>'

    hook = p.get('hook_text', '')
    return f"""
<div style="background:#14151B;border:1px solid #191B23;padding:14px 16px;margin-bottom:6px;">
  <div style="display:flex;align-items:flex-start;gap:8px;margin-bottom:8px;">
    <span style="font-size:9px;font-weight:600;letter-spacing:.04em;padding:2px 6px;border-radius:2px;background:{bg};color:{fg};margin-top:2px;white-space:nowrap;">{label}</span>
    <div style="font-family:'Cormorant Garamond',serif;font-size:16px;font-weight:500;line-height:1.25;color:#E8E5DF;">{p.get('title','')}</div>
  </div>
  <div style="font-size:10px;color:#5C606E;margin-bottom:6px;">{p.get('estimated_duration','')} · {p.get('vibe','')}</div>
  <div style="font-size:11px;color:#A8ABBA;border-left:2px solid #C9A74A;padding-left:8px;margin-bottom:10px;line-height:1.45;font-style:italic;">{hook}</div>
  <div style="display:flex;gap:3px;align-items:center;">{sb}</div>
</div>"""

def export_group_csv(group):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["#", "Filename", "Duration(s)", "Category", "Mood",
                     "Lighting", "Quality", "Transition to next", "Notes"])
    clips = group.get("clips", [])
    for i, c in enumerate(clips):
        writer.writerow([
            i + 1,
            c.get("filename", ""),
            c.get("duration_seconds", ""),
            c.get("category", ""),
            c.get("mood", ""),
            c.get("lighting", ""),
            c.get("quality_score", ""),
            c.get("transition_note", ""),
            c.get("notes", ""),
        ])
    return buf.getvalue()

def export_group_shotlist(group):
    clips = group.get("clips", [])
    lines = [f"SEQUENCE: {group['name']}", f"Type: {group['group_type']}"]
    if group.get("arc"):
        lines += [f"Arc: {group['arc']}", ""]
    lines += ["─" * 50]
    for i, c in enumerate(clips):
        dur = f"{c.get('duration_seconds', 0):.1f}s"
        qs  = c.get('quality_score') or 0
        lines.append(f"{i+1:02d}. {c.get('filename','')}  [{dur}]  Q:{qs}/10")
        meta = " · ".join(filter(None, [c.get("category"), c.get("mood"), c.get("lighting","").replace("_"," ")]))
        if meta: lines.append(f"    {meta}")
        if c.get("notes"): lines.append(f"    {c['notes']}")
        if c.get("transition_note") and i < len(clips) - 1:
            lines.append(f"    → {c['transition_note']}")
        lines.append("")
    if group.get("edit_notes"):
        lines += ["─" * 50, "EDIT NOTES", group["edit_notes"]]
    return "\n".join(lines)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
<div style="padding:4px 0 16px 0;border-bottom:1px solid #22242E;margin-bottom:16px;">
  <span style="font-family:'Cormorant Garamond',serif;font-size:24px;font-weight:500;color:#E8E5DF;letter-spacing:.01em;">
    <em style="color:#C9A74A;font-style:italic;">F</em>ootage
  </span>
</div>""", unsafe_allow_html=True)

    folder_input = st.text_input("Folder path", placeholder="/Users/you/Videos/trip")
    col1, col2   = st.columns(2)
    scan_btn     = col1.button("Scan",    use_container_width=True, type="primary")
    proposal_btn = col2.button("Propose", use_container_width=True)

    st.divider()
    show_unusable   = st.checkbox("Show unusable clips", value=False)
    # dynamic category list includes custom categories
    filter_category = st.selectbox("Category", all_category_options())
    filter_mood     = st.selectbox("Mood", ["All","cinematic","energetic","peaceful","dramatic",
                                            "intimate","adventurous","romantic","other"])
    proposal_count  = st.slider("Proposals to generate", 3, 10, 5)

    st.divider()
    st.markdown('<div style="font-size:10px;color:#5C606E;text-transform:uppercase;letter-spacing:.04em;margin-bottom:8px;">Custom Categories</div>', unsafe_allow_html=True)
    for cc in cache.get_custom_categories(DB_PATH):
        c1, c2 = st.columns([4, 1])
        c1.markdown(f'<div style="font-size:11px;color:#A8ABBA;padding:3px 0;">{cc["name"]}</div>', unsafe_allow_html=True)
        if c2.button("✕", key=f"del_cat_{cc['id']}"):
            cache.delete_custom_category(cc["name"], DB_PATH)
            st.rerun()
    with st.form("new_cat_form", clear_on_submit=True):
        new_cat = st.text_input("New category", placeholder="e.g. transitions", label_visibility="collapsed")
        if st.form_submit_button("Add", use_container_width=True) and new_cat.strip():
            cache.add_custom_category(new_cat.strip(), db_path=DB_PATH)
            st.rerun()

    # quality summary
    all_clips_sb = cache.get_all_clips(usable_only=False, db_path=DB_PATH)
    if all_clips_sb:
        total = len(all_clips_sb)
        hi  = sum(1 for c in all_clips_sb if (c.get('quality_score') or 0) >= 7)
        mid = sum(1 for c in all_clips_sb if 5 <= (c.get('quality_score') or 0) < 7)
        lo  = sum(1 for c in all_clips_sb if 0 < (c.get('quality_score') or 0) < 5)
        pct = lambda n: f"{round(n/total*100)}%" if total else "0%"
        st.markdown(f"""
<div style="background:#14151B;border:1px solid #22242E;border-radius:4px;padding:10px 12px;margin-top:12px;">
  <div style="font-size:10px;color:#5C606E;margin-bottom:8px;">Quality distribution</div>
  <div style="display:flex;align-items:center;gap:6px;margin-bottom:5px;">
    <span style="font-size:10px;color:#5DBB8A;width:28px;text-align:right;">High</span>
    <div style="flex:1;height:3px;background:#272A36;border-radius:1px;"><div style="width:{pct(hi)};height:100%;background:#5DBB8A;border-radius:1px;"></div></div>
    <span style="font-size:10px;color:#5C606E;">{hi}</span>
  </div>
  <div style="display:flex;align-items:center;gap:6px;margin-bottom:5px;">
    <span style="font-size:10px;color:#D98C3E;width:28px;text-align:right;">Mid</span>
    <div style="flex:1;height:3px;background:#272A36;border-radius:1px;"><div style="width:{pct(mid)};height:100%;background:#D98C3E;border-radius:1px;"></div></div>
    <span style="font-size:10px;color:#5C606E;">{mid}</span>
  </div>
  <div style="display:flex;align-items:center;gap:6px;">
    <span style="font-size:10px;color:#CC4E4E;width:28px;text-align:right;">Low</span>
    <div style="flex:1;height:3px;background:#272A36;border-radius:1px;"><div style="width:{pct(lo)};height:100%;background:#CC4E4E;border-radius:1px;"></div></div>
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
            custom_cat_names = [c["name"] for c in cache.get_custom_categories(DB_PATH)]

            for i, vpath in enumerate(videos):
                progress.progress((i + 1) / len(videos))
                status_text.text(f"Processing {vpath.name}…")

                md5      = cache.compute_md5(str(vpath))
                existing = cache.get_clip_by_hash(md5, DB_PATH)
                if existing and existing.get("analyzed_at"):
                    continue

                meta = extractor.get_video_metadata(str(vpath))
                cache.upsert_clip({"filepath": str(vpath), "filename": vpath.name,
                                   "md5_hash": md5, **meta, "analyzed_at": None}, db_path=DB_PATH)

                frame_paths = extractor.extract_frames(str(vpath), max_frames=6)
                analysis    = claude_client.analyze_frames(
                    frame_paths,
                    custom_categories=custom_cat_names or None
                ) if frame_paths else {}
                extractor.cleanup_frames(frame_paths)
                extractor.extract_thumbnail(str(vpath), THUMBS_DIR, md5)

                now = datetime.now().isoformat()
                if analysis and "error" not in analysis:
                    cache.upsert_clip({"filepath": str(vpath), "filename": vpath.name,
                                       "md5_hash": md5, **meta, "analyzed_at": now,
                                       "quality_score": analysis.get("quality_score"),
                                       "scene_type":    analysis.get("scene_type"),
                                       "mood":          analysis.get("mood"),
                                       "lighting":      analysis.get("lighting"),
                                       "activities":    json.dumps(analysis.get("activities", [])),
                                       "category":      analysis.get("category"),
                                       "is_usable":     1 if analysis.get("is_usable", True) else 0,
                                       "notes":         analysis.get("notes"),
                                       "raw_analysis":  json.dumps(analysis)}, db_path=DB_PATH)
                else:
                    cache.upsert_clip({"filepath": str(vpath), "filename": vpath.name,
                                       "md5_hash": md5, **meta, "analyzed_at": now,
                                       "is_usable": 0,
                                       "notes": analysis.get("error", "analysis failed")}, db_path=DB_PATH)

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
    filtered  = [
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

        cat_order = ["travel","golden-hour","action","nature","b-roll","portrait","food","architecture","other"]
        # prepend any custom categories that have clips
        custom_names = [c["name"] for c in cache.get_custom_categories(DB_PATH)]
        full_order   = custom_names + [c for c in cat_order if c not in custom_names]

        by_cat = {}
        for c in filtered:
            by_cat.setdefault(c.get("category") or "other", []).append(c)

        for cat in full_order + [k for k in by_cat if k not in full_order]:
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

                        st.markdown('<div style="height:1px;background:#191B23;margin:8px 0 6px;"></div>', unsafe_allow_html=True)

                        # category reassignment
                        all_cats = [c for c in all_category_options() if c != "All"]
                        cur_cat  = clip.get("category") or "other"
                        new_cat  = st.selectbox("Category", all_cats,
                                                index=all_cats.index(cur_cat) if cur_cat in all_cats else 0,
                                                key=f"cat_{clip['id']}")
                        if new_cat != cur_cat:
                            if st.button("Apply category", key=f"apply_cat_{clip['id']}"):
                                cache.set_clip_category(clip["id"], new_cat, DB_PATH)
                                st.rerun()

                        # add to group
                        groups_lib = cache.get_groups(DB_PATH)
                        if groups_lib:
                            g_opts  = [f"{g['name']} ({g['clip_count']} clips)" for g in groups_lib]
                            chosen  = st.selectbox("Add to group", ["—"] + g_opts, key=f"grp_{clip['id']}")
                            if chosen != "—":
                                gidx = g_opts.index(chosen)
                                if st.button("Add to group", key=f"add_grp_{clip['id']}"):
                                    cache.add_clip_to_group(groups_lib[gidx]["id"], clip["id"], DB_PATH)
                                    st.success("Added.")

                        st.markdown('<div style="height:1px;background:#191B23;margin:8px 0 6px;"></div>', unsafe_allow_html=True)

                        # re-analyze
                        col_ra, col_dl = st.columns(2)
                        if col_ra.button("Re-analyze", key=f"reanalyze_{clip['id']}"):
                            vpath = Path(clip["filepath"])
                            if not vpath.exists():
                                st.error("File not found on disk.")
                            else:
                                with st.spinner(f"Re-analyzing {clip['filename']}…"):
                                    custom_cat_names = [c["name"] for c in cache.get_custom_categories(DB_PATH)]
                                    frame_paths = extractor.extract_frames(str(vpath), max_frames=6)
                                    analysis    = claude_client.analyze_frames(
                                        frame_paths,
                                        custom_categories=custom_cat_names or None
                                    ) if frame_paths else {}
                                    extractor.cleanup_frames(frame_paths)
                                    extractor.extract_thumbnail(str(vpath), THUMBS_DIR, clip["md5_hash"])
                                if analysis and "error" not in analysis:
                                    cache.upsert_clip({**clip,
                                                       "analyzed_at":  datetime.now().isoformat(),
                                                       "quality_score": analysis.get("quality_score"),
                                                       "scene_type":    analysis.get("scene_type"),
                                                       "mood":          analysis.get("mood"),
                                                       "lighting":      analysis.get("lighting"),
                                                       "activities":    json.dumps(analysis.get("activities", [])),
                                                       "category":      analysis.get("category"),
                                                       "is_usable":     1 if analysis.get("is_usable", True) else 0,
                                                       "notes":         analysis.get("notes"),
                                                       "raw_analysis":  json.dumps(analysis)}, db_path=DB_PATH)
                                    st.success("Re-analyzed.")
                                    st.rerun()
                                else:
                                    st.error(analysis.get("error", "Analysis failed."))

                        # delete
                        if col_dl.button("Delete", key=f"del_{clip['id']}"):
                            st.session_state[f"confirm_del_{clip['id']}"] = True

                        if st.session_state.get(f"confirm_del_{clip['id']}"):
                            st.warning("Delete this clip from the database?")
                            cc1, cc2 = st.columns(2)
                            if cc1.button("Yes, delete", key=f"yes_del_{clip['id']}"):
                                cache.delete_clip(clip["id"], DB_PATH)
                                st.session_state.pop(f"confirm_del_{clip['id']}", None)
                                st.rerun()
                            if cc2.button("Cancel", key=f"no_del_{clip['id']}"):
                                st.session_state.pop(f"confirm_del_{clip['id']}", None)
                                st.rerun()


# ── Proposals ─────────────────────────────────────────────────────────────────
with tab_proposals:
    all_proposals = cache.get_proposals(db_path=DB_PATH)

    if not all_proposals:
        st.markdown('<div style="color:#5C606E;font-size:12px;padding:24px 0;">No proposals yet. Click Propose after scanning clips.</div>', unsafe_allow_html=True)
    else:
        fcol1, fcol2 = st.columns(2)
        platform_filter = fcol1.selectbox("Platform", ["All","tiktok","reels","youtube-shorts","youtube"],
                                          key="platform_filter")
        status_filter   = fcol2.selectbox("Status", ["Active (saved)", "Done", "All"],
                                          key="status_filter")

        clips_by_id = {c["id"]: c for c in cache.get_all_clips(db_path=DB_PATH)}

        for p in all_proposals:
            if platform_filter != "All" and p.get("platform") != platform_filter:
                continue
            pstatus = p.get("status", "saved")
            if status_filter == "Active (saved)" and pstatus != "saved":
                continue
            if status_filter == "Done" and pstatus != "done":
                continue

            clip_ids   = json.loads(p.get("clip_ids") or "[]")
            clip_md5s  = [clips_by_id[cid]["md5_hash"] for cid in clip_ids if cid in clips_by_id]
            clip_names = [clips_by_id[cid]["filename"] for cid in clip_ids if cid in clips_by_id]

            st.markdown(proposal_card_html(p, clip_md5s), unsafe_allow_html=True)

            col_caption, col_clips, col_done, col_dismiss = st.columns([2, 2, 1, 1])
            with col_caption:
                with st.expander("Caption & hashtags"):
                    st.write(p.get("caption", ""))
                    tags = json.loads(p.get("hashtags") or "[]")
                    if tags:
                        st.write(" ".join(f"#{h}" for h in tags))
            with col_clips:
                with st.expander(f"Clips ({len(clip_names)})"):
                    for n in clip_names:
                        st.markdown(f'<div style="font-size:11px;color:#A8ABBA;">→ {n}</div>', unsafe_allow_html=True)
            with col_done:
                done_label = "↩ Unsave" if pstatus == "done" else "✓ Done"
                if st.button(done_label, key=f"done_{p['id']}"):
                    new_status = "saved" if pstatus == "done" else "done"
                    cache.update_proposal_status(p['id'], new_status, DB_PATH)
                    st.rerun()
            with col_dismiss:
                if st.button("Dismiss", key=f"dismiss_{p['id']}"):
                    cache.update_proposal_status(p['id'], "dismissed", DB_PATH)
                    st.rerun()

            st.markdown('<div style="height:1px;background:#191B23;margin:4px 0 12px 0;"></div>', unsafe_allow_html=True)


# ── Groups ────────────────────────────────────────────────────────────────────
with tab_groups:
    all_groups = cache.get_groups(DB_PATH)

    col_g1, col_g2 = st.columns([4, 1])
    col_g1.markdown('<div style="font-family:\'Cormorant Garamond\',serif;font-style:italic;font-size:22px;font-weight:400;color:#E8E5DF;margin-bottom:12px;">Sequences & Groups</div>', unsafe_allow_html=True)
    if col_g2.button("New Group", type="primary"):
        st.session_state["creating_group"] = True

    if st.session_state.get("creating_group"):
        with st.form("new_group_form"):
            g_name = st.text_input("Group name", placeholder="e.g. Golden Hour Arc")
            g_type = st.selectbox("Type", ["custom","transition-sequence","narrative-arc",
                                           "b-roll-pack","timeline","mood-reel","opening-sequence"])
            g_desc = st.text_input("Description (optional)")
            cs, cc = st.columns(2)
            if cs.form_submit_button("Create", use_container_width=True) and g_name.strip():
                cache.create_group(g_name.strip(), g_type, g_desc, DB_PATH)
                st.session_state["creating_group"] = False
                st.rerun()
            if cc.form_submit_button("Cancel", use_container_width=True):
                st.session_state["creating_group"] = False
                st.rerun()

    if not all_groups:
        st.markdown('<div style="color:#5C606E;font-size:12px;padding:16px 0;">No groups yet. Create a group, then add clips from the Library tab.</div>', unsafe_allow_html=True)
    else:
        open_id = st.session_state.get("open_group")

        if open_id:
            group = cache.get_group(open_id, DB_PATH)
            if not group:
                st.session_state.pop("open_group")
                st.rerun()

            # header
            hc1, hc2, hc3, hc4 = st.columns([1, 4, 1, 1])
            if hc1.button("← Back"):
                st.session_state.pop("open_group", None)
                st.rerun()
            hc2.markdown(f'<div style="padding-top:6px;"><span style="font-family:\'Cormorant Garamond\',serif;font-style:italic;font-size:20px;color:#E8E5DF;">{group["name"]}</span> <span style="font-size:10px;color:#5C606E;">{group["group_type"]}</span></div>', unsafe_allow_html=True)
            if hc3.button("Rename"):
                st.session_state["renaming_group"] = open_id
            if hc4.button("Delete group"):
                cache.delete_group(open_id, DB_PATH)
                st.session_state.pop("open_group", None)
                st.rerun()

            # rename form
            if st.session_state.get("renaming_group") == open_id:
                with st.form("rename_group_form"):
                    new_name = st.text_input("New name", value=group["name"])
                    rc1, rc2 = st.columns(2)
                    if rc1.form_submit_button("Save", use_container_width=True) and new_name.strip():
                        cache.rename_group(open_id, new_name.strip(), DB_PATH)
                        st.session_state.pop("renaming_group", None)
                        st.rerun()
                    if rc2.form_submit_button("Cancel", use_container_width=True):
                        st.session_state.pop("renaming_group", None)
                        st.rerun()

            if group.get("description"):
                st.caption(group["description"])

            clips_in_group = group.get("clips", [])

            if not clips_in_group:
                st.markdown('<div style="color:#5C606E;font-size:12px;padding:12px 0;">No clips yet. Add clips from the Library tab.</div>', unsafe_allow_html=True)
            else:
                # analysis results banner
                if group.get("arc") or group.get("edit_notes"):
                    st.markdown(f"""
<div style="background:#14151B;border:1px solid #191B23;padding:12px 14px;margin-bottom:14px;">
  <div style="font-size:10px;color:#5C606E;margin-bottom:6px;text-transform:uppercase;letter-spacing:.04em;">Sequence Analysis</div>
  {f'<div style="font-size:12px;color:#C9A74A;font-style:italic;margin-bottom:6px;">{group["arc"]}</div>' if group.get("arc") else ""}
  {f'<div style="font-size:11px;color:#A8ABBA;line-height:1.5;">{group["edit_notes"]}</div>' if group.get("edit_notes") else ""}
</div>""", unsafe_allow_html=True)

                # export row
                ec1, ec2, ec3 = st.columns([2, 2, 4])
                csv_data  = export_group_csv(group)
                shot_data = export_group_shotlist(group)
                ec1.download_button("Export CSV", csv_data,
                                    file_name=f"{group['name'].replace(' ','_')}.csv",
                                    mime="text/csv", use_container_width=True)
                ec2.download_button("Export Shot List", shot_data,
                                    file_name=f"{group['name'].replace(' ','_')}_shotlist.txt",
                                    mime="text/plain", use_container_width=True)

                st.markdown('<div style="font-size:10px;color:#5C606E;margin:12px 0 8px;text-transform:uppercase;letter-spacing:.04em;">Clips in sequence order</div>', unsafe_allow_html=True)
                clip_ids_ordered = [c["id"] for c in clips_in_group]

                for idx, clip in enumerate(clips_in_group):
                    qs = clip.get("quality_score") or 0
                    qc = quality_color(qs)
                    b64 = thumb_b64(clip.get("md5_hash", ""))
                    thumb_html = f'<img src="data:image/jpeg;base64,{b64}" style="width:60px;height:34px;object-fit:cover;display:block;">' if b64 else '<div style="width:60px;height:34px;background:#1D1F28;"></div>'
                    t_note = clip.get("transition_note", "")

                    c_num, c_img, c_info, c_up, c_dn, c_rm = st.columns([0.4, 1.2, 5, 0.6, 0.6, 0.8])
                    c_num.markdown(f'<div style="font-size:14px;color:#5C606E;font-family:\'Cormorant Garamond\',serif;font-style:italic;padding-top:8px;">{idx+1}</div>', unsafe_allow_html=True)
                    c_img.markdown(f'<div style="border-top:3px solid {qc};margin-top:6px;">{thumb_html}</div>', unsafe_allow_html=True)
                    c_info.markdown(f"""
<div style="padding:4px 0;">
  <div style="font-size:11px;color:#E8E5DF;">{clip['filename']}</div>
  <div style="font-size:10px;color:#5C606E;">{clip.get('mood','')} · {clip.get('lighting','').replace('_',' ')}</div>
  {f'<div style="font-size:10px;color:#C9A74A;font-style:italic;margin-top:2px;">→ {t_note}</div>' if t_note else ''}
</div>""", unsafe_allow_html=True)

                    if c_up.button("↑", key=f"up_{open_id}_{clip['id']}", disabled=(idx == 0)):
                        o = clip_ids_ordered[:]
                        o[idx], o[idx-1] = o[idx-1], o[idx]
                        cache.reorder_group(open_id, o, DB_PATH)
                        st.rerun()
                    if c_dn.button("↓", key=f"dn_{open_id}_{clip['id']}", disabled=(idx == len(clips_in_group)-1)):
                        o = clip_ids_ordered[:]
                        o[idx], o[idx+1] = o[idx+1], o[idx]
                        cache.reorder_group(open_id, o, DB_PATH)
                        st.rerun()
                    if c_rm.button("Remove", key=f"rm_{open_id}_{clip['id']}"):
                        cache.remove_clip_from_group(open_id, clip["id"], DB_PATH)
                        st.rerun()

                st.markdown('<div style="height:1px;background:#191B23;margin:12px 0;"></div>', unsafe_allow_html=True)

                if st.button("Analyze Sequence with Claude", type="primary"):
                    with st.spinner("Analyzing sequence…"):
                        result = claude_client.analyze_group_sequence(clips_in_group)
                    if "error" not in result:
                        suggested = result.get("suggested_order", [])
                        valid_ids  = set(clip_ids_ordered)
                        if suggested and all(i in valid_ids for i in suggested):
                            cache.reorder_group(open_id, suggested, DB_PATH)
                        cache.update_group_analysis(
                            open_id,
                            result.get("arc", ""),
                            f"{result.get('edit_notes','')}\nEstimated duration: {result.get('estimated_total_duration','')}",
                            result.get("transitions", []),
                            DB_PATH
                        )
                        st.success("Sequence analyzed. Order and transitions updated.")
                        st.rerun()
                    else:
                        st.error(f"Analysis failed: {result.get('error')}")

        else:
            # group list
            TYPE_COLORS = {
                "transition-sequence": "#58A6FF", "narrative-arc": "#C9A74A",
                "b-roll-pack": "#7A7D8A", "timeline": "#5DBB8A",
                "mood-reel": "#C264B8", "opening-sequence": "#D98C3E", "custom": "#5C606E",
            }
            cols = st.columns(3)
            for i, g in enumerate(all_groups):
                tc = TYPE_COLORS.get(g["group_type"], "#5C606E")
                badge_analyzed = '<span style="font-size:9px;color:#5DBB8A;margin-left:6px;">✓ analyzed</span>' if g.get("analyzed_at") else ''
                with cols[i % 3]:
                    st.markdown(f"""
<div style="background:#14151B;border:1px solid #191B23;padding:12px 14px;margin-bottom:8px;">
  <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
    <span style="font-size:9px;padding:1px 6px;background:{tc}18;color:{tc};border-radius:2px;font-weight:600;">{g['group_type'].replace('-',' ').upper()}</span>
    {badge_analyzed}
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
    th1, th2 = st.columns([3, 1])
    th1.caption(f"Cached · {age}" if age else "No trend data. Click Fetch.")
    fetch_btn = th2.button("Fetch Trends", type="primary")

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

        tf1, tf2 = st.columns(2)
        filter_trend_plat = tf1.selectbox("Platform", ["All","tiktok","reels","youtube-shorts"], key="trend_plat")
        filter_trend_cat  = tf2.selectbox("Category",  ["All"] + CATEGORY_ORDER, key="trend_cat")

        for cat in CATEGORY_ORDER:
            if filter_trend_cat != "All" and filter_trend_cat != cat:
                continue
            cat_trends = [t for t in trend_data
                          if t.get("category") == cat
                          and (filter_trend_plat == "All" or t.get("platform") in (filter_trend_plat, "all"))]
            if not cat_trends:
                continue

            st.markdown(f'<div style="font-family:\'Cormorant Garamond\',serif;font-style:italic;font-size:18px;font-weight:400;color:#E8E5DF;margin:20px 0 10px 0;">{CATEGORY_LABELS.get(cat, cat)}</div>', unsafe_allow_html=True)
            tcols = st.columns(2)
            for i, t in enumerate(cat_trends):
                pop  = t.get("popularity", "")
                icon = POP_ICON.get(pop, "")
                plat = PLATFORM_MAP.get(t.get("platform", "all"), "ALL")
                with tcols[i % 2]:
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
