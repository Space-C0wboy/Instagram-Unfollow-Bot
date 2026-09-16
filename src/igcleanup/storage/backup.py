import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from igcleanup.storage.accounts import Account

COLUMNS = ["username", "profile_url", "display_name", "last_post_date", "status"]

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _escape_formula(value: str) -> str:
    if value and value[0] in _FORMULA_PREFIXES:
        return "'" + value
    return value


@dataclass
class BackupRow:
    username: str
    profile_url: str
    display_name: str
    last_post_date: str
    status: str


def write_backup(backups_dir: Path, accounts: list[Account], now: datetime) -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    path = backups_dir / f"unfollow-backup-{now:%Y-%m-%d-%H%M}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for a in accounts:
            w.writerow([
                _escape_formula(a.username), a.profile_url, _escape_formula(a.display_name),
                a.last_post_date.isoformat() if a.last_post_date else "", a.status,
            ])
    return path


def write_rows(path: Path, rows: list["BackupRow"]) -> Path:
    """Write already-escaped backup rows to `path` in the backup format."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([r.username, r.profile_url, r.display_name, r.last_post_date, r.status])
    return path


def read_backup(path: Path) -> list[BackupRow]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [BackupRow(**{c: row[c] for c in COLUMNS}) for row in csv.DictReader(f)]


def list_backups(backups_dir: Path) -> list[Path]:
    if not backups_dir.is_dir():
        return []
    return sorted(backups_dir.glob("unfollow-backup-*.csv"), key=lambda p: p.name, reverse=True)
