import sqlite3
import hashlib
import json
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / "data" / "clips.db"

BASE_CATEGORIES = ["travel", "b-roll", "portrait", "action", "golden-hour",
                   "food", "architecture", "nature", "other"]


def init_db(db_path: Path = DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            md5_hash TEXT NOT NULL,
            duration_seconds REAL,
            resolution TEXT,
            file_size_mb REAL,
            analyzed_at TEXT,
            quality_score INTEGER,
            scene_type TEXT,
            mood TEXT,
            lighting TEXT,
            activities TEXT,
            category TEXT,
            is_usable INTEGER DEFAULT 1,
            notes TEXT,
            raw_analysis TEXT
        );

        CREATE TABLE IF NOT EXISTS proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            title TEXT,
            platform TEXT,
            estimated_duration TEXT,
            clip_ids TEXT,
            hook_text TEXT,
            caption TEXT,
            hashtags TEXT,
            vibe TEXT,
            status TEXT DEFAULT 'saved'
        );

        CREATE TABLE IF NOT EXISTS trends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at TEXT NOT NULL,
            name TEXT,
            platform TEXT,
            category TEXT,
            description TEXT,
            relevance_to_travel TEXT,
            popularity TEXT,
            view_indicator TEXT
        );

        CREATE TABLE IF NOT EXISTS custom_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            color TEXT DEFAULT '#888888',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS clip_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            group_type TEXT DEFAULT 'custom',
            description TEXT,
            arc TEXT,
            edit_notes TEXT,
            analyzed_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS clip_group_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            clip_id INTEGER NOT NULL,
            position INTEGER DEFAULT 0,
            transition_note TEXT,
            FOREIGN KEY(group_id) REFERENCES clip_groups(id) ON DELETE CASCADE,
            FOREIGN KEY(clip_id) REFERENCES clips(id),
            UNIQUE(group_id, clip_id)
        );
    """)
    conn.commit()
    conn.close()


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def compute_md5(filepath: str) -> str:
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        h.update(f.read(1024 * 1024))  # first 1MB only for speed
    return h.hexdigest()


def get_clip_by_hash(md5: str, db_path: Path = DB_PATH) -> dict | None:
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM clips WHERE md5_hash = ?", (md5,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_clip_by_path(filepath: str, db_path: Path = DB_PATH) -> dict | None:
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM clips WHERE filepath = ?", (filepath,)).fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_clip(clip_data: dict, db_path: Path = DB_PATH):
    conn = get_connection(db_path)
    fields = [
        "filepath", "filename", "md5_hash", "duration_seconds", "resolution",
        "file_size_mb", "analyzed_at", "quality_score", "scene_type", "mood",
        "lighting", "activities", "category", "is_usable", "notes", "raw_analysis"
    ]
    data = {k: clip_data.get(k) for k in fields}
    placeholders = ", ".join(f":{k}" for k in fields)
    updates = ", ".join(f"{k} = :{k}" for k in fields if k != "filepath")
    conn.execute(
        f"INSERT INTO clips ({', '.join(fields)}) VALUES ({placeholders}) "
        f"ON CONFLICT(filepath) DO UPDATE SET {updates}",
        data
    )
    conn.commit()
    conn.close()


def get_all_clips(usable_only: bool = False, db_path: Path = DB_PATH) -> list[dict]:
    conn = get_connection(db_path)
    query = "SELECT * FROM clips WHERE analyzed_at IS NOT NULL"
    if usable_only:
        query += " AND is_usable = 1"
    query += " ORDER BY quality_score DESC"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_clip(clip_id: int, db_path: Path = DB_PATH):
    conn = get_connection(db_path)
    conn.execute("DELETE FROM clip_group_members WHERE clip_id = ?", (clip_id,))
    conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    conn.commit()
    conn.close()


def get_pending_clips(db_path: Path = DB_PATH) -> list[dict]:
    conn = get_connection(db_path)
    rows = conn.execute("SELECT * FROM clips WHERE analyzed_at IS NULL").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_proposals(proposals: list[dict], db_path: Path = DB_PATH):
    conn = get_connection(db_path)
    now = datetime.now().isoformat()
    for p in proposals:
        conn.execute(
            """INSERT INTO proposals
               (created_at, title, platform, estimated_duration, clip_ids,
                hook_text, caption, hashtags, vibe, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'saved')""",
            (
                now, p.get("title"), p.get("platform"), p.get("estimated_duration"),
                json.dumps(p.get("clip_ids", [])), p.get("hook_text"), p.get("caption"),
                json.dumps(p.get("hashtags", [])), p.get("vibe")
            )
        )
    conn.commit()
    conn.close()


def get_proposals(status: str | None = None, db_path: Path = DB_PATH) -> list[dict]:
    conn = get_connection(db_path)
    if status:
        rows = conn.execute(
            "SELECT * FROM proposals WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM proposals WHERE status != 'dismissed' ORDER BY created_at DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_proposal_status(proposal_id: int, status: str, db_path: Path = DB_PATH):
    conn = get_connection(db_path)
    conn.execute("UPDATE proposals SET status = ? WHERE id = ?", (status, proposal_id))
    conn.commit()
    conn.close()


def clear_proposals(db_path: Path = DB_PATH):
    conn = get_connection(db_path)
    conn.execute("DELETE FROM proposals")
    conn.commit()
    conn.close()


# ── Trends ────────────────────────────────────────────────────────────────────

def get_cached_trends(max_age_hours: int = 24, db_path: Path = DB_PATH) -> list[dict] | None:
    conn = get_connection(db_path)
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
    rows = conn.execute(
        "SELECT * FROM trends WHERE fetched_at > ? ORDER BY id ASC", (cutoff,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows] if rows else None


def save_trends(trends: list[dict], db_path: Path = DB_PATH):
    conn = get_connection(db_path)
    conn.execute("DELETE FROM trends")  # replace, don't accumulate
    now = datetime.now().isoformat()
    for t in trends:
        conn.execute(
            """INSERT INTO trends
               (fetched_at, name, platform, category, description,
                relevance_to_travel, popularity, view_indicator)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now, t.get("name"), t.get("platform"), t.get("category"),
                t.get("description"), t.get("relevance_to_travel"),
                t.get("popularity"), t.get("view_indicator"),
            )
        )
    conn.commit()
    conn.close()


# ── Custom categories ─────────────────────────────────────────────────────────

def get_custom_categories(db_path: Path = DB_PATH) -> list[dict]:
    conn = get_connection(db_path)
    rows = conn.execute("SELECT * FROM custom_categories ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_custom_category(name: str, color: str = "#888888", db_path: Path = DB_PATH):
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO custom_categories (name, color, created_at) VALUES (?, ?, ?)",
            (name.lower().strip(), color, datetime.now().isoformat())
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # already exists
    conn.close()


def delete_custom_category(name: str, db_path: Path = DB_PATH):
    conn = get_connection(db_path)
    conn.execute("DELETE FROM custom_categories WHERE name = ?", (name,))
    conn.commit()
    conn.close()


def set_clip_category(clip_id: int, category: str, db_path: Path = DB_PATH):
    conn = get_connection(db_path)
    conn.execute("UPDATE clips SET category = ? WHERE id = ?", (category, clip_id))
    conn.commit()
    conn.close()


# ── Groups ────────────────────────────────────────────────────────────────────

def create_group(name: str, group_type: str = "custom", description: str = "",
                 db_path: Path = DB_PATH) -> int:
    conn = get_connection(db_path)
    cur = conn.execute(
        "INSERT INTO clip_groups (name, group_type, description, created_at) VALUES (?, ?, ?, ?)",
        (name, group_type, description, datetime.now().isoformat())
    )
    group_id = cur.lastrowid
    conn.commit()
    conn.close()
    return group_id


def get_groups(db_path: Path = DB_PATH) -> list[dict]:
    conn = get_connection(db_path)
    rows = conn.execute("SELECT * FROM clip_groups ORDER BY created_at DESC").fetchall()
    groups = []
    for row in rows:
        g = dict(row)
        count = conn.execute(
            "SELECT COUNT(*) as n FROM clip_group_members WHERE group_id = ?", (g["id"],)
        ).fetchone()["n"]
        g["clip_count"] = count
        groups.append(g)
    conn.close()
    return groups


def get_group(group_id: int, db_path: Path = DB_PATH) -> dict | None:
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM clip_groups WHERE id = ?", (group_id,)).fetchone()
    if not row:
        conn.close()
        return None
    g = dict(row)
    members = conn.execute(
        """SELECT m.position, m.transition_note, c.*
           FROM clip_group_members m
           JOIN clips c ON c.id = m.clip_id
           WHERE m.group_id = ?
           ORDER BY m.position""",
        (group_id,)
    ).fetchall()
    g["clips"] = [dict(r) for r in members]
    conn.close()
    return g


def add_clip_to_group(group_id: int, clip_id: int, db_path: Path = DB_PATH):
    conn = get_connection(db_path)
    max_pos = conn.execute(
        "SELECT COALESCE(MAX(position), -1) as p FROM clip_group_members WHERE group_id = ?",
        (group_id,)
    ).fetchone()["p"]
    try:
        conn.execute(
            "INSERT INTO clip_group_members (group_id, clip_id, position) VALUES (?, ?, ?)",
            (group_id, clip_id, max_pos + 1)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # already in group
    conn.close()


def remove_clip_from_group(group_id: int, clip_id: int, db_path: Path = DB_PATH):
    conn = get_connection(db_path)
    conn.execute(
        "DELETE FROM clip_group_members WHERE group_id = ? AND clip_id = ?",
        (group_id, clip_id)
    )
    conn.commit()
    # re-number positions
    members = conn.execute(
        "SELECT id FROM clip_group_members WHERE group_id = ? ORDER BY position", (group_id,)
    ).fetchall()
    for i, m in enumerate(members):
        conn.execute("UPDATE clip_group_members SET position = ? WHERE id = ?", (i, m["id"]))
    conn.commit()
    conn.close()


def reorder_group(group_id: int, clip_ids_ordered: list[int], db_path: Path = DB_PATH):
    conn = get_connection(db_path)
    for i, clip_id in enumerate(clip_ids_ordered):
        conn.execute(
            "UPDATE clip_group_members SET position = ? WHERE group_id = ? AND clip_id = ?",
            (i, group_id, clip_id)
        )
    conn.commit()
    conn.close()


def update_group_analysis(group_id: int, arc: str, edit_notes: str,
                          transitions: list[dict], db_path: Path = DB_PATH):
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE clip_groups SET arc = ?, edit_notes = ?, analyzed_at = ? WHERE id = ?",
        (arc, edit_notes, datetime.now().isoformat(), group_id)
    )
    for t in transitions:
        conn.execute(
            """UPDATE clip_group_members SET transition_note = ?
               WHERE group_id = ? AND clip_id = ?""",
            (t.get("note", ""), group_id, t.get("from_clip"))
        )
    conn.commit()
    conn.close()


def rename_group(group_id: int, name: str, db_path: Path = DB_PATH):
    conn = get_connection(db_path)
    conn.execute("UPDATE clip_groups SET name = ? WHERE id = ?", (name, group_id))
    conn.commit()
    conn.close()


def delete_group(group_id: int, db_path: Path = DB_PATH):
    conn = get_connection(db_path)
    conn.execute("DELETE FROM clip_group_members WHERE group_id = ?", (group_id,))
    conn.execute("DELETE FROM clip_groups WHERE id = ?", (group_id,))
    conn.commit()
    conn.close()


def get_trends_age(db_path: Path = DB_PATH) -> str | None:
    """Return human-readable age of cached trends, or None if no cache."""
    conn = get_connection(db_path)
    row = conn.execute("SELECT fetched_at FROM trends ORDER BY fetched_at DESC LIMIT 1").fetchone()
    conn.close()
    if not row:
        return None
    fetched = datetime.fromisoformat(row["fetched_at"])
    diff = datetime.now() - fetched
    mins = int(diff.total_seconds() / 60)
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    return f"{hours}h ago"
