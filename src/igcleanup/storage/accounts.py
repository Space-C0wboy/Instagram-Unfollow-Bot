import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

PROFILE_URL = "https://www.instagram.com/{username}/"
DEFAULT_SELECTED_STATUSES = ("inactive", "gone")


@dataclass
class Account:
    username: str
    display_name: str
    profile_url: str
    profile_pic_url: str | None
    post_count: int | None
    last_post_date: date | None
    status: str
    checked_at: datetime | None
    selected: bool
    unfollowed_at: datetime | None


def _row_to_account(r: sqlite3.Row) -> Account:
    return Account(
        username=r["username"],
        display_name=r["display_name"],
        profile_url=r["profile_url"],
        profile_pic_url=r["profile_pic_url"],
        post_count=r["post_count"],
        last_post_date=date.fromisoformat(r["last_post_date"]) if r["last_post_date"] else None,
        status=r["status"],
        checked_at=datetime.fromisoformat(r["checked_at"]) if r["checked_at"] else None,
        selected=bool(r["selected"]),
        unfollowed_at=datetime.fromisoformat(r["unfollowed_at"]) if r["unfollowed_at"] else None,
    )


class AccountRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert_following(self, pairs: Iterable[tuple[str, str]]) -> None:
        with self.conn:
            self.conn.executemany(
                """INSERT INTO accounts (username, display_name, profile_url)
                   VALUES (?, ?, ?)
                   ON CONFLICT(username) DO UPDATE SET display_name = excluded.display_name""",
                [(u, d, PROFILE_URL.format(username=u)) for u, d in pairs],
            )

    def pending_usernames(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT username FROM accounts WHERE status = 'pending' ORDER BY username"
        ).fetchall()
        return [r["username"] for r in rows]

    def set_result(
        self, username: str, *, status: str, post_count: int | None = None,
        last_post_date: date | None = None, profile_pic_url: str | None = None,
        display_name: str | None = None, checked_at: datetime,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE accounts SET status = ?, post_count = ?, last_post_date = ?,
                   profile_pic_url = COALESCE(?, profile_pic_url),
                   display_name = COALESCE(?, display_name), checked_at = ?
                   WHERE username = ?""",
                (status, post_count, last_post_date.isoformat() if last_post_date else None,
                 profile_pic_url, display_name, checked_at.isoformat(), username),
            )

    def set_pending(self, username: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE accounts SET status = 'pending' WHERE username = ?", (username,)
            )

    def set_status(self, username: str, status: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE accounts SET status = ? WHERE username = ?", (status, username)
            )

    def get(self, username: str) -> Account | None:
        r = self.conn.execute("SELECT * FROM accounts WHERE username = ?", (username,)).fetchone()
        return _row_to_account(r) if r else None

    def all(self) -> list[Account]:
        rows = self.conn.execute("SELECT * FROM accounts ORDER BY username").fetchall()
        return [_row_to_account(r) for r in rows]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]

    def set_selected(self, usernames: list[str], selected: bool) -> None:
        with self.conn:
            self.conn.executemany(
                "UPDATE accounts SET selected = ? WHERE username = ?",
                [(int(selected), u) for u in usernames],
            )

    def apply_default_selection(self, since: datetime | None = None) -> None:
        """Tick inactive/gone rows and untick the rest; with `since`, only rows checked at or
        after that moment, so ticks the user changed on older rows are left alone."""
        placeholders = ",".join("?" for _ in DEFAULT_SELECTED_STATUSES)
        where = " WHERE checked_at >= ?" if since is not None else ""
        params = (*DEFAULT_SELECTED_STATUSES, since.isoformat()) if since is not None else DEFAULT_SELECTED_STATUSES
        with self.conn:
            self.conn.execute(
                f"UPDATE accounts SET selected = CASE WHEN status IN ({placeholders}) "
                f"AND username NOT IN (SELECT username FROM kept) THEN 1 ELSE 0 END{where}",
                params,
            )

    def mark_stale_pending(self, older_than: datetime) -> int:
        """Queue for re-checking: never checked, checked before `older_than`, or left in error."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE accounts SET status = 'pending' WHERE unfollowed_at IS NULL "
                "AND (checked_at IS NULL OR checked_at < ? OR status = 'error')",
                (older_than.isoformat(),),
            )
        return cur.rowcount

    def remove_not_in(self, usernames: Iterable[str]) -> int:
        """Drop rows for accounts the user no longer follows (they are absent from a fresh listing)."""
        keep = set(usernames)
        gone = [r["username"] for r in self.conn.execute("SELECT username FROM accounts").fetchall()
                if r["username"] not in keep]
        with self.conn:
            self.conn.executemany("DELETE FROM accounts WHERE username = ?", [(u,) for u in gone])
        return len(gone)

    def unchecked_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM accounts WHERE status = 'pending'").fetchone()[0]

    def reset_all(self) -> None:
        """Start over completely: results and the keep list."""
        with self.conn:
            self.conn.execute("DELETE FROM accounts")
            self.conn.execute("DELETE FROM kept")

    def targets(self) -> list[Account]:
        rows = self.conn.execute(
            "SELECT * FROM accounts WHERE selected = 1 AND unfollowed_at IS NULL "
            "AND status != 'active' AND username NOT IN (SELECT username FROM kept) ORDER BY username"
        ).fetchall()
        return [_row_to_account(r) for r in rows]

    def is_target(self, username: str) -> bool:
        """Still ticked, not kept, not already unfollowed: re-checked right before each unfollow."""
        row = self.conn.execute(
            "SELECT 1 FROM accounts WHERE username = ? AND selected = 1 AND unfollowed_at IS NULL "
            "AND status != 'active' AND username NOT IN (SELECT username FROM kept)", (username,)
        ).fetchone()
        return row is not None

    # The keep list lives in its own table so it survives "Scan again", which
    # deletes and rebuilds the accounts table.
    def keep(self, username: str, at: datetime) -> None:
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO kept (username, at) VALUES (?, ?)",
                              (username, at.isoformat()))
            self.conn.execute("UPDATE accounts SET selected = 0 WHERE username = ?", (username,))

    def unkeep(self, username: str) -> None:
        placeholders = ",".join("?" for _ in DEFAULT_SELECTED_STATUSES)
        with self.conn:
            self.conn.execute("DELETE FROM kept WHERE username = ?", (username,))
            self.conn.execute(
                f"UPDATE accounts SET selected = CASE WHEN status IN ({placeholders}) THEN 1 ELSE 0 END "
                "WHERE username = ?", (*DEFAULT_SELECTED_STATUSES, username))

    def kept_usernames(self) -> set[str]:
        return {r["username"] for r in self.conn.execute("SELECT username FROM kept").fetchall()}

    def status_counts(self) -> dict[str, int]:
        """How many accounts sit in each review group right now (kept ones counted apart)."""
        rows = self.conn.execute(
            "SELECT CASE WHEN username IN (SELECT username FROM kept) THEN 'kept' ELSE status END AS g, "
            "COUNT(*) AS n FROM accounts WHERE unfollowed_at IS NULL GROUP BY g"
        ).fetchall()
        return {r["g"]: r["n"] for r in rows}

    def mark_unfollowed(self, username: str, at: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE accounts SET unfollowed_at = ? WHERE username = ?",
                (at.isoformat(), username),
            )

    def rebucket(self, threshold: date) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE accounts
                   SET status = CASE WHEN last_post_date < ? THEN 'inactive' ELSE 'active' END
                   WHERE status IN ('active', 'inactive') AND last_post_date IS NOT NULL""",
                (threshold.isoformat(),),
            )
            # An account that just became active must not stay ticked from an earlier bucket.
            self.conn.execute("UPDATE accounts SET selected = 0 WHERE status = 'active'")

    def reset_for_rescan(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM accounts")
