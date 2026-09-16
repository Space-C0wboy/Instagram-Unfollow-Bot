import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    username        TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL DEFAULT '',
    profile_url     TEXT NOT NULL,
    profile_pic_url TEXT,
    post_count      INTEGER,
    last_post_date  TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    checked_at      TEXT,
    selected        INTEGER NOT NULL DEFAULT 0,
    unfollowed_at   TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,
    state       TEXT NOT NULL,
    total       INTEGER NOT NULL DEFAULT 0,
    done        INTEGER NOT NULL DEFAULT 0,
    message     TEXT,
    source      TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS kept (
    username TEXT PRIMARY KEY,
    at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id   INTEGER NOT NULL,
    username TEXT NOT NULL,
    action   TEXT NOT NULL,
    at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_at ON events(at);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn
