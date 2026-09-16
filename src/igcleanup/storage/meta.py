import sqlite3


class MetaRepo:
    """Small key/value store for facts about this installation (e.g. which account it is for)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def delete(self, key: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM meta WHERE key = ?", (key,))
