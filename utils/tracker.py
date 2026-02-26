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
    "interviewing", "offer", "rejected", "withdrawn", "archived"
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
    """Log a newly discovered job."""
    conn = get_db()
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
    """Mark a job as applied."""
    status = "applied" if success else "failed"
    conn = get_db()
    conn.execute("""
        UPDATE applications
        SET status = ?, applied_at = ?
        WHERE id = ?
    """, (status, datetime.now().isoformat(), job_id))
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
    for status in ["discovered", "matched", "applied", "skipped", "failed"]:
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
    conn.execute("UPDATE applications SET status = ? WHERE id = ?", (status, job_id))
    conn.commit()
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
