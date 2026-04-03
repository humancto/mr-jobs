"""
Application Tracker — SQLite-backed log of all job discovery and applications.
Prevents duplicate applications and provides stats.
Extended with dashboard-ready queries, schema migration, and event broadcasting.
"""

import sqlite3
import json
from datetime import datetime, date
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "applications.db"

# All valid statuses
VALID_STATUSES = [
    "discovered", "matched", "applied", "skipped", "failed",
    "interviewing", "offer", "rejected", "withdrawn", "archived", "ignored"
]


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            platform TEXT,
            url TEXT,
            apply_url TEXT,
            location TEXT,
            match_score INTEGER,
            reasoning TEXT,
            cover_letter TEXT,
            status TEXT DEFAULT 'discovered',
            applied_at TIMESTAMP,
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_status ON applications(status)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_company ON applications(company)
    """)
    # Ignore-list: hashes of jobs to skip on future discovery runs
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ignored_hashes (
            hash TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            ignored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Schema migration: add new columns if they don't exist
    _migrate_schema(conn)
    conn.commit()
    return conn


def _migrate_schema(conn: sqlite3.Connection):
    """Add new columns for extended tracking (safe to run multiple times)."""
    cursor = conn.execute("PRAGMA table_info(applications)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    migrations = {
        "salary_min": "INTEGER",
        "salary_max": "INTEGER",
        "date_posted": "TEXT",
        "source": "TEXT DEFAULT ''",
        "notes": "TEXT DEFAULT ''",
        "tags": "TEXT DEFAULT ''",
        "description": "TEXT DEFAULT ''",
        "tailored_resume": "TEXT DEFAULT ''",
        "follow_up_date": "TEXT",
        "last_activity": "TEXT",
        "follow_up_count": "INTEGER DEFAULT 0",
    }

    for col, col_type in migrations.items():
        if col not in existing_cols:
            try:
                conn.execute(f"ALTER TABLE applications ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass  # Column already exists


def is_already_seen(job_id: str) -> bool:
    """Check if we've already seen this job."""
    conn = get_db()
    row = conn.execute("SELECT id FROM applications WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return row is not None


def _emit(event_type: str, data=None):
    """Emit event if EventBus is available."""
    try:
        from utils.events import EventBus
        EventBus.emit(event_type, data)
    except Exception:
        pass


def log_discovered(job) -> None:
    """Log a newly discovered job. Skips if on the ignore list."""
    # Check ignore list first (fast hash lookup)
    if is_ignored(job.title, job.company):
        return

    conn = get_db()
    try:
        metadata = json.dumps(job.metadata) if isinstance(job.metadata, dict) else job.metadata
        conn.execute("""
            INSERT OR IGNORE INTO applications
            (id, title, company, platform, url, apply_url, location, description, source,
             salary_min, salary_max, date_posted, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job.id, job.title, job.company, job.platform, job.url, job.apply_url,
            job.location, getattr(job, 'description', ''),
            job.metadata.get('source', job.platform) if isinstance(job.metadata, dict) else job.platform,
            job.metadata.get('salary_min') if isinstance(job.metadata, dict) else None,
            job.metadata.get('salary_max') if isinstance(job.metadata, dict) else None,
            job.metadata.get('date_posted', '') if isinstance(job.metadata, dict) else '',
            metadata
        ))
        conn.commit()
    finally:
        conn.close()
    _emit("job_discovered", {"id": job.id, "title": job.title, "company": job.company})


def log_matched(job_id: str, score: int, reasoning: str, cover_letter: str) -> None:
    """Update a job with its match results."""
    conn = get_db()
    conn.execute("""
        UPDATE applications
        SET match_score = ?, reasoning = ?, cover_letter = ?, status = 'matched'
        WHERE id = ?
    """, (score, reasoning, cover_letter, job_id))
    conn.commit()
    conn.close()
    _emit("job_matched", {"id": job_id, "score": score})


def log_applied(job_id: str, success: bool) -> None:
    """Mark a job as applied and set follow-up if successful."""
    status = "applied" if success else "failed"
    now = datetime.now().isoformat()
    conn = get_db()
    if success:
        from datetime import timedelta
        follow_up = (datetime.now() + timedelta(days=7)).isoformat()
        conn.execute("""
            UPDATE applications
            SET status = ?, applied_at = ?, last_activity = ?, follow_up_date = ?
            WHERE id = ?
        """, (status, now, now, follow_up, job_id))
    else:
        conn.execute("""
            UPDATE applications
            SET status = ?, applied_at = ?
            WHERE id = ?
        """, (status, now, job_id))
    conn.commit()
    conn.close()
    _emit("job_applied", {"id": job_id, "success": success})


def log_skipped(job_id: str, reason: str) -> None:
    """Mark a job as skipped."""
    conn = get_db()
    conn.execute("""
        UPDATE applications
        SET status = 'skipped', reasoning = ?
        WHERE id = ?
    """, (reason, job_id))
    conn.commit()
    conn.close()


def get_today_count() -> int:
    """How many applications have been submitted today."""
    conn = get_db()
    row = conn.execute("""
        SELECT COUNT(*) as cnt FROM applications
        WHERE status = 'applied' AND DATE(applied_at) = DATE('now')
    """).fetchone()
    conn.close()
    return row["cnt"]


def get_stats() -> dict:
    """Get overall application stats."""
    conn = get_db()
    stats = {}
    for status in VALID_STATUSES:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM applications WHERE status = ?", (status,)
        ).fetchone()
        stats[status] = row["cnt"]

    stats["today"] = get_today_count()

    # Average match score for applied jobs
    row = conn.execute("""
        SELECT AVG(match_score) as avg_score FROM applications
        WHERE status = 'applied' AND match_score IS NOT NULL
    """).fetchone()
    stats["avg_match_score"] = round(row["avg_score"] or 0, 1)

    conn.close()
    return stats


def print_stats():
    """Pretty print stats."""
    s = get_stats()
    print(f"\n📊 Application Stats:")
    print(f"   Discovered: {s['discovered']}")
    print(f"   Matched:    {s['matched']}")
    print(f"   Applied:    {s['applied']} (today: {s['today']})")
    print(f"   Skipped:    {s['skipped']}")
    print(f"   Failed:     {s['failed']}")
    if s['avg_match_score']:
        print(f"   Avg Score:  {s['avg_match_score']}")


def reset_unscored() -> int:
    """Reset jobs with NULL match_score back to 'discovered' for re-processing."""
    conn = get_db()
    cursor = conn.execute("""
        UPDATE applications SET status = 'discovered'
        WHERE match_score IS NULL AND status != 'applied'
    """)
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count


def delete_all() -> int:
    """Delete all jobs for a fresh start."""
    conn = get_db()
    cursor = conn.execute("DELETE FROM applications")
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count


def get_jobs_by_status(status: str) -> list:
    """Get all jobs with a given status."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM applications WHERE status = ?", (status,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_unscored_jobs() -> list:
    """Get all jobs that need scoring (discovered with no score)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM applications WHERE match_score IS NULL AND status = 'discovered'"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_all_jobs(
    status: str = None,
    company: str = None,
    min_score: int = None,
    search: str = None,
    sort_by: str = "discovered_at",
    sort_order: str = "desc",
    limit: int = 100,
    offset: int = 0
) -> tuple:
    """Get jobs with filtering, sorting, and pagination. Returns (jobs, total_count)."""
    conn = get_db()
    try:
        conditions = []
        params = []

        if status:
            conditions.append("status = ?")
            params.append(status)
        if company:
            conditions.append("company LIKE ?")
            params.append(f"%{company}%")
        if min_score is not None:
            conditions.append("match_score >= ?")
            params.append(min_score)
        if search:
            conditions.append("(title LIKE ? OR company LIKE ? OR location LIKE ?)")
            params.extend([f"%{search}%"] * 3)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Validate sort column
        valid_sorts = ["discovered_at", "match_score", "company", "title", "status", "applied_at"]
        if sort_by not in valid_sorts:
            sort_by = "discovered_at"
        if sort_order not in ("asc", "desc"):
            sort_order = "desc"

        # Get total count
        count_row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM applications {where}", params
        ).fetchone()
        total = count_row["cnt"]

        # Get paginated results
        rows = conn.execute(
            f"SELECT * FROM applications {where} ORDER BY {sort_by} {sort_order} LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
    finally:
        conn.close()

    return [dict(row) for row in rows], total


def get_job_by_id(job_id: str) -> dict:
    """Get a single job by ID."""
    conn = get_db()
    row = conn.execute("SELECT * FROM applications WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_job_status(job_id: str, status: str) -> bool:
    """Update a job's status."""
    if status not in VALID_STATUSES:
        return False
    conn = get_db()
    try:
        conn.execute("UPDATE applications SET status = ? WHERE id = ?", (status, job_id))
        conn.commit()
    finally:
        conn.close()
    _emit("job_status_changed", {"id": job_id, "status": status})
    return True


def update_job_notes(job_id: str, notes: str) -> bool:
    """Update notes for a job."""
    conn = get_db()
    conn.execute("UPDATE applications SET notes = ? WHERE id = ?", (notes, job_id))
    conn.commit()
    conn.close()
    return True


def update_apply_url(job_id: str, apply_url: str) -> bool:
    """Update a job's apply_url (after URL resolution)."""
    conn = get_db()
    conn.execute("UPDATE applications SET apply_url = ? WHERE id = ?", (apply_url, job_id))
    conn.commit()
    conn.close()
    return True


def delete_job(job_id: str) -> bool:
    """Delete a single job."""
    conn = get_db()
    cursor = conn.execute("DELETE FROM applications WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def get_timeline_stats() -> list:
    """Get application counts grouped by date for charts."""
    conn = get_db()
    rows = conn.execute("""
        SELECT DATE(discovered_at) as date,
               COUNT(*) as total,
               SUM(CASE WHEN status = 'applied' THEN 1 ELSE 0 END) as applied,
               SUM(CASE WHEN status = 'matched' THEN 1 ELSE 0 END) as matched
        FROM applications
        WHERE discovered_at IS NOT NULL
        GROUP BY DATE(discovered_at)
        ORDER BY date DESC
        LIMIT 30
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_score_distribution() -> list:
    """Get score distribution for charts."""
    conn = get_db()
    rows = conn.execute("""
        SELECT
            CASE
                WHEN match_score >= 90 THEN '90-100'
                WHEN match_score >= 80 THEN '80-89'
                WHEN match_score >= 70 THEN '70-79'
                WHEN match_score >= 60 THEN '60-69'
                WHEN match_score >= 50 THEN '50-59'
                WHEN match_score < 50 THEN '0-49'
                ELSE 'Unscored'
            END as bracket,
            COUNT(*) as count
        FROM applications
        GROUP BY bracket
        ORDER BY bracket DESC
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_companies() -> list:
    """Get distinct company names for filter dropdown."""
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT company FROM applications ORDER BY company"
    ).fetchall()
    conn.close()
    return [row["company"] for row in rows]


def update_tailored_resume(job_id: str, tailored_data: dict) -> bool:
    """Store tailored resume data for a job."""
    conn = get_db()
    conn.execute(
        "UPDATE applications SET tailored_resume = ? WHERE id = ?",
        (json.dumps(tailored_data), job_id)
    )
    conn.commit()
    conn.close()
    _emit("tailor_complete", {"id": job_id})
    return True


def get_tailored_resume(job_id: str) -> dict:
    """Get tailored resume data for a job."""
    conn = get_db()
    row = conn.execute(
        "SELECT tailored_resume FROM applications WHERE id = ?", (job_id,)
    ).fetchone()
    conn.close()
    if row and row["tailored_resume"]:
        try:
            return json.loads(row["tailored_resume"])
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


# ---------------------------------------------------------------------------
# Follow-up reminders & ghost detection
# ---------------------------------------------------------------------------


def set_follow_up(job_id: str, days: int = 7) -> bool:
    """Set a follow-up reminder date for a job."""
    from datetime import timedelta
    follow_up = (datetime.now() + timedelta(days=days)).isoformat()
    conn = get_db()
    conn.execute(
        "UPDATE applications SET follow_up_date = ?, last_activity = ? WHERE id = ?",
        (follow_up, datetime.now().isoformat(), job_id)
    )
    conn.commit()
    conn.close()
    _emit("follow_up_set", {"id": job_id, "date": follow_up})
    return True


def get_overdue_follow_ups() -> list:
    """Get jobs past their follow-up date that are still active (applied/interviewing)."""
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM applications
        WHERE follow_up_date IS NOT NULL
          AND follow_up_date <= datetime('now')
          AND status IN ('applied', 'interviewing')
        ORDER BY follow_up_date ASC
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_ghost_alerts(days: int = 14) -> list:
    """Get jobs applied more than N days ago with no status change (potential ghosts)."""
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM applications
        WHERE status = 'applied'
          AND applied_at IS NOT NULL
          AND datetime(applied_at, '+' || ? || ' days') <= datetime('now')
          AND (last_activity IS NULL OR datetime(last_activity, '+' || ? || ' days') <= datetime('now'))
        ORDER BY applied_at ASC
    """, (days, days)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def increment_follow_up(job_id: str, days: int = 7) -> bool:
    """Mark a follow-up as done and schedule the next one."""
    from datetime import timedelta
    conn = get_db()
    conn.execute("""
        UPDATE applications
        SET follow_up_count = COALESCE(follow_up_count, 0) + 1,
            follow_up_date = ?,
            last_activity = ?
        WHERE id = ?
    """, (
        (datetime.now() + timedelta(days=days)).isoformat(),
        datetime.now().isoformat(),
        job_id,
    ))
    conn.commit()
    conn.close()
    _emit("follow_up_done", {"id": job_id})
    return True


def dismiss_follow_up(job_id: str) -> bool:
    """Clear follow-up for a job."""
    conn = get_db()
    conn.execute(
        "UPDATE applications SET follow_up_date = NULL WHERE id = ?",
        (job_id,)
    )
    conn.commit()
    conn.close()
    return True


# ---------------------------------------------------------------------------
# Ignore list — hash-based dedup across discovery runs
# ---------------------------------------------------------------------------

import hashlib

def _job_hash(title: str, company: str) -> str:
    """Generate a stable hash from normalized title + company."""
    key = f"{title.lower().strip()}|{company.lower().strip()}"
    return hashlib.md5(key.encode()).hexdigest()


def is_ignored(title: str, company: str) -> bool:
    """Check if a job (by title+company) is in the ignore list."""
    conn = get_db()
    h = _job_hash(title, company)
    row = conn.execute("SELECT hash FROM ignored_hashes WHERE hash = ?", (h,)).fetchone()
    conn.close()
    return row is not None


def ignore_jobs(job_ids: list) -> int:
    """Mark jobs as ignored and add their hashes to the ignore list."""
    conn = get_db()
    count = 0
    for jid in job_ids:
        row = conn.execute("SELECT title, company FROM applications WHERE id = ?", (jid,)).fetchone()
        if row:
            h = _job_hash(row["title"], row["company"])
            conn.execute(
                "INSERT OR IGNORE INTO ignored_hashes (hash, title, company) VALUES (?, ?, ?)",
                (h, row["title"], row["company"])
            )
            conn.execute("UPDATE applications SET status = 'ignored' WHERE id = ?", (jid,))
            count += 1
    conn.commit()
    conn.close()
    _emit("jobs_ignored", {"count": count})
    return count


def purge_all() -> dict:
    """Nuke all discovery data but preserve the ignore list."""
    conn = get_db()
    job_count = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    conn.execute("DELETE FROM applications")
    conn.commit()
    conn.close()
    _emit("purged", {"jobs_deleted": job_count})
    return {"jobs_deleted": job_count}


def purge_everything() -> dict:
    """Nuke ALL data including ignore list — full factory reset."""
    conn = get_db()
    job_count = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    ignore_count = conn.execute("SELECT COUNT(*) FROM ignored_hashes").fetchone()[0]
    conn.execute("DELETE FROM applications")
    conn.execute("DELETE FROM ignored_hashes")
    conn.commit()
    conn.close()
    _emit("purged", {"jobs_deleted": job_count, "ignores_cleared": ignore_count})
    return {"jobs_deleted": job_count, "ignores_cleared": ignore_count}


def get_ignored_count() -> int:
    """Count of hashes in the ignore list."""
    conn = get_db()
    row = conn.execute("SELECT COUNT(*) FROM ignored_hashes").fetchone()
    conn.close()
    return row[0]


# ---------------------------------------------------------------------------
# Interview sessions — mock interview tracking
# ---------------------------------------------------------------------------


def _ensure_interview_table(conn: sqlite3.Connection):
    """Create interview_sessions table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS interview_sessions (
            session_id TEXT PRIMARY KEY,
            job_id TEXT DEFAULT '',
            job_title TEXT DEFAULT '',
            company TEXT DEFAULT '',
            interview_type TEXT DEFAULT 'mixed',
            difficulty TEXT DEFAULT 'mid',
            duration_minutes REAL DEFAULT 0,
            questions_asked INTEGER DEFAULT 0,
            state TEXT DEFAULT 'ended',
            started_at TEXT,
            ended_at TEXT,
            transcript TEXT DEFAULT '[]',
            evaluation TEXT DEFAULT '{}',
            video_enabled INTEGER DEFAULT 0,
            engagement_scores TEXT DEFAULT '[]',
            recording_path TEXT DEFAULT '',
            provider TEXT DEFAULT '',
            mode TEXT DEFAULT 'text',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_interview_job
        ON interview_sessions(job_id)
    """)
    # Migrate: add columns if missing (for existing DBs)
    for col, default in [("recording_path", "''"), ("provider", "''"), ("mode", "'text'")]:
        try:
            conn.execute(f"ALTER TABLE interview_sessions ADD COLUMN {col} TEXT DEFAULT {default}")
        except Exception:
            pass  # Column already exists


def save_interview_session(session_data: dict) -> bool:
    """Save an interview session to the database."""
    conn = get_db()
    try:
        _ensure_interview_table(conn)

        conn.execute("""
            INSERT OR REPLACE INTO interview_sessions
            (session_id, job_id, job_title, company, interview_type, difficulty,
             duration_minutes, questions_asked, state, started_at, ended_at,
             transcript, evaluation, video_enabled, engagement_scores,
             recording_path, provider, mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_data.get("session_id", ""),
            session_data.get("job_id", ""),
            session_data.get("job_title", ""),
            session_data.get("company", ""),
            session_data.get("interview_type", "mixed"),
            session_data.get("difficulty", "mid"),
            session_data.get("duration_minutes", 0),
            session_data.get("questions_asked", 0),
            session_data.get("state", "ended"),
            session_data.get("started_at"),
            session_data.get("ended_at"),
            json.dumps(session_data.get("transcript", [])),
            json.dumps(session_data.get("evaluation", {})),
            1 if session_data.get("video_enabled") else 0,
            json.dumps(session_data.get("engagement_scores", [])),
            session_data.get("recording_path", ""),
            session_data.get("provider", ""),
            session_data.get("mode", "text"),
        ))
        conn.commit()
    finally:
        conn.close()
    _emit("interview_completed", {
        "session_id": session_data.get("session_id"),
        "job_title": session_data.get("job_title"),
        "company": session_data.get("company"),
    })
    return True


def get_interview_sessions(job_id: str = None) -> list:
    """Get interview sessions, optionally filtered by job_id."""
    conn = get_db()
    try:
        _ensure_interview_table(conn)

        if job_id:
            rows = conn.execute(
                "SELECT * FROM interview_sessions WHERE job_id = ? ORDER BY created_at DESC",
                (job_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM interview_sessions ORDER BY created_at DESC"
            ).fetchall()
    finally:
        conn.close()

    sessions = []
    for row in rows:
        d = dict(row)
        # Parse JSON fields
        for field in ("transcript", "evaluation", "engagement_scores"):
            if isinstance(d.get(field), str):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        sessions.append(d)
    return sessions


def get_interview_session(session_id: str) -> dict:
    """Get a single interview session by ID."""
    conn = get_db()
    _ensure_interview_table(conn)

    row = conn.execute(
        "SELECT * FROM interview_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    conn.close()

    if not row:
        return None

    d = dict(row)
    for field in ("transcript", "evaluation", "engagement_scores"):
        if isinstance(d.get(field), str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d
