import sqlite3
from dataclasses import dataclass
from datetime import date, datetime

_UNSET = object()


@dataclass
class Job:
    id: int
    type: str
    state: str
    total: int
    done: int
    message: str | None
    source: str | None
    started_at: datetime
    finished_at: datetime | None


def _row_to_job(r: sqlite3.Row) -> Job:
    return Job(
        id=r["id"], type=r["type"], state=r["state"], total=r["total"], done=r["done"],
        message=r["message"], source=r["source"],
        started_at=datetime.fromisoformat(r["started_at"]),
        finished_at=datetime.fromisoformat(r["finished_at"]) if r["finished_at"] else None,
    )


class JobRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, type: str, total: int, started_at: datetime | None = None,
               source: str | None = None) -> Job:
        started_at = started_at or datetime.now()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO jobs (type, state, total, done, source, started_at) "
                "VALUES (?, 'running', ?, 0, ?, ?)",
                (type, total, source, started_at.isoformat()),
            )
        return self.get(cur.lastrowid)

    def update(self, job_id: int, *, state=None, total=None, done=None,
               message=_UNSET, finished_at=None) -> None:
        sets, params = [], []
        if state is not None:
            sets.append("state = ?"); params.append(state)
        if total is not None:
            sets.append("total = ?"); params.append(total)
        if done is not None:
            sets.append("done = ?"); params.append(done)
        if message is not _UNSET:
            sets.append("message = ?"); params.append(message)
        if finished_at is not None:
            sets.append("finished_at = ?"); params.append(finished_at.isoformat())
        if not sets:
            return
        params.append(job_id)
        with self.conn:
            self.conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)

    def get(self, job_id: int) -> Job:
        return _row_to_job(self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

    def latest(self, type: str) -> Job | None:
        r = self.conn.execute(
            "SELECT * FROM jobs WHERE type = ? ORDER BY id DESC LIMIT 1", (type,)
        ).fetchone()
        return _row_to_job(r) if r else None

    def active(self) -> Job | None:
        r = self.conn.execute(
            "SELECT * FROM jobs WHERE state IN ('running', 'paused') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _row_to_job(r) if r else None

    def latest_failed(self) -> Job | None:
        r = self.conn.execute(
            "SELECT * FROM jobs WHERE state = 'failed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _row_to_job(r) if r else None

    def pause_stale_running(self) -> None:
        with self.conn:
            self.conn.execute("UPDATE jobs SET state='paused', message=NULL WHERE state='running'")


class EventRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def record(self, job_id: int, username: str, action: str, at: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO events (job_id, username, action, at) VALUES (?, ?, ?, ?)",
                (job_id, username, action, at.isoformat()),
            )

    def last_action_at(self) -> datetime | None:
        row = self.conn.execute("SELECT MAX(at) AS at FROM events").fetchone()
        return datetime.fromisoformat(row["at"]) if row and row["at"] else None

    def count_on(self, day: date) -> int:
        prefix = day.isoformat()
        return self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE substr(at, 1, 10) = ?", (prefix,)
        ).fetchone()[0]

    def usernames_for_job(self, job_id: int) -> set[str]:
        rows = self.conn.execute("SELECT username FROM events WHERE job_id = ?", (job_id,)).fetchall()
        return {r["username"] for r in rows}
