"""
SQLite database layer for report job persistence.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import aiosqlite

DATABASE_PATH = os.environ.get("DATABASE_PATH", "./reports.db")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    mist_user_id TEXT NOT NULL,
    org_id TEXT NOT NULL,
    site_id TEXT NOT NULL,
    site_name TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    progress TEXT DEFAULT '{}',
    result TEXT,
    error TEXT,
    include_cable_tests INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_user_created ON reports(mist_user_id, created_at);
"""


async def init_db() -> None:
    """Create tables if they do not exist."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript(_CREATE_TABLE_SQL)
        await db.commit()


def _row_to_dict(row, description) -> dict:
    """Convert a sqlite3.Row (or tuple) + cursor.description to a dict."""
    return {description[i][0]: row[i] for i in range(len(description))}


def _deserialize_row(row: dict) -> dict:
    """Parse JSON fields (progress, result) back to dicts/None."""
    progress = row.get("progress", "{}")
    if isinstance(progress, str):
        try:
            row["progress"] = json.loads(progress)
        except Exception:
            row["progress"] = {}
    else:
        row["progress"] = progress or {}

    result = row.get("result")
    if isinstance(result, str):
        try:
            row["result"] = json.loads(result)
        except Exception:
            row["result"] = None
    return row


async def create_job(
    job_id: str,
    mist_user_id: str,
    org_id: str,
    site_id: str,
    include_cable_tests: bool = False,
) -> dict:
    """Insert a new report job and return it as a dict."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """
            INSERT INTO reports (id, mist_user_id, org_id, site_id, status, progress, include_cable_tests, created_at)
            VALUES (?, ?, ?, ?, 'pending', '{}', ?, ?)
            """,
            (job_id, mist_user_id, org_id, site_id, int(include_cable_tests), now),
        )
        await db.commit()
    return {
        "id": job_id,
        "mist_user_id": mist_user_id,
        "org_id": org_id,
        "site_id": site_id,
        "site_name": "",
        "status": "pending",
        "progress": {},
        "result": None,
        "error": None,
        "include_cable_tests": int(include_cable_tests),
        "created_at": now,
        "completed_at": None,
    }


async def get_job(job_id: str) -> dict | None:
    """Fetch a single job by ID. Returns None if not found."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT * FROM reports WHERE id = ?", (job_id,)) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return _deserialize_row(_row_to_dict(row, cursor.description))


async def update_job(job_id: str, **kwargs) -> None:
    """Update arbitrary fields on a job row."""
    if not kwargs:
        return

    # Serialize dict fields to JSON strings
    for key in ("progress", "result"):
        if key in kwargs and isinstance(kwargs[key], dict):
            kwargs[key] = json.dumps(kwargs[key])

    set_clauses = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [job_id]

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(f"UPDATE reports SET {set_clauses} WHERE id = ?", values)
        await db.commit()


async def list_user_jobs(mist_user_id: str) -> list[dict]:
    """Return all jobs for a user in the last 24h, most recent first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute(
            "SELECT * FROM reports WHERE mist_user_id = ? AND created_at >= ? ORDER BY created_at DESC",
            (mist_user_id, cutoff),
        ) as cursor:
            rows = await cursor.fetchall()
            description = cursor.description
    return [_deserialize_row(_row_to_dict(row, description)) for row in rows]


async def cleanup_old_jobs() -> None:
    """Delete report rows older than 24 hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("DELETE FROM reports WHERE created_at < ?", (cutoff,))
        await db.commit()
