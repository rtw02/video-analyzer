import sqlite3
import hashlib
import json
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / "data" / "clips.db"


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
