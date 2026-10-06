"""A small SQLite database that remembers every job the agent has seen.

This is what stops the agent from scoring or applying to the same job twice.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .sources import Job

# Lifecycle of a job:
#   new -> filtered_out                     (failed the keyword/location filters)
#   new -> scored -> applying -> applied    (the happy path)
#                            \-> needs_human / declined / failed
#   any -> skipped                          (you chose to skip it)
STATUSES = (
    "new",
    "filtered_out",
    "scored",
    "applying",
    "applied",
    "needs_human",
    "declined",
    "failed",
    "skipped",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    company      TEXT NOT NULL,
    title        TEXT NOT NULL,
    location     TEXT,
    url          TEXT,
    apply_url    TEXT,
    description  TEXT,
    remote       INTEGER DEFAULT 0,
    posted_at    TEXT,
    found_at     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'new',
    score        INTEGER,
    score_detail TEXT,
    notes        TEXT,
    updated_at   TEXT
);
CREATE TABLE IF NOT EXISTS boards (
    board_type TEXT NOT NULL,
    company    TEXT NOT NULL,
    origin     TEXT NOT NULL,
    added_at   TEXT NOT NULL,
    PRIMARY KEY (board_type, company)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ jobs

    def add_job(self, job: Job, status: str = "new", notes: str = "") -> bool:
        """Save a job. Returns False if it was already saved earlier."""
        cursor = self.db.execute(
            """INSERT OR IGNORE INTO jobs
               (id, source, company, title, location, url, apply_url, description,
                remote, posted_at, found_at, status, notes, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.id, job.source, job.company, job.title, job.location, job.url,
                job.apply_url, job.description, int(job.remote), job.posted_at,
                _now(), status, notes, _now(),
            ),
        )
        self.db.commit()
        return cursor.rowcount == 1

    def get(self, job_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def set_status(self, job_id: str, status: str, notes: str | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"Unknown status {status!r}. Use one of: {', '.join(STATUSES)}")
        if notes is None:
            self.db.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), job_id)
            )
        else:
            self.db.execute(
                "UPDATE jobs SET status = ?, notes = ?, updated_at = ? WHERE id = ?",
                (status, notes, _now(), job_id),
            )
        self.db.commit()

    def set_score(self, job_id: str, score: int, detail: dict) -> None:
        self.db.execute(
            "UPDATE jobs SET status = 'scored', score = ?, score_detail = ?, updated_at = ? WHERE id = ?",
            (score, json.dumps(detail), _now(), job_id),
        )
        self.db.commit()

    def jobs_with_status(self, status: str, limit: int | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM jobs WHERE status = ? ORDER BY found_at DESC"
        params: tuple = (status,)
        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)
        return self.db.execute(sql, params).fetchall()

    def best_unapplied(self, min_score: int, limit: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM jobs WHERE status = 'scored' AND score >= ? ORDER BY score DESC LIMIT ?",
            (min_score, limit),
        ).fetchall()

    def all_jobs(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return self.jobs_with_status(status)
        return self.db.execute(
            "SELECT * FROM jobs ORDER BY COALESCE(score, -1) DESC, found_at DESC"
        ).fetchall()

    def counts(self) -> dict[str, int]:
        rows = self.db.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        return {row["status"]: row["n"] for row in rows}

    # ------------------------------------------------------------ boards

    def add_board(self, board_type: str, company: str, origin: str) -> bool:
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO boards (board_type, company, origin, added_at) VALUES (?, ?, ?, ?)",
            (board_type, company, origin, _now()),
        )
        self.db.commit()
        return cursor.rowcount == 1

    def boards(self) -> list[tuple[str, str]]:
        rows = self.db.execute(
            "SELECT board_type, company FROM boards ORDER BY board_type, company"
        ).fetchall()
        return [(row["board_type"], row["company"]) for row in rows]
