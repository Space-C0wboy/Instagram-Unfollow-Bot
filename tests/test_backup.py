import csv
from datetime import date, datetime
from pathlib import Path

from igcleanup.storage.accounts import Account
from igcleanup.storage.backup import list_backups, read_backup, write_backup


def _acct(username, status="inactive", last=date(2024, 5, 1)):
    return Account(
        username=username, display_name=f"Name {username}",
        profile_url=f"https://www.instagram.com/{username}/",
        profile_pic_url=None, post_count=1, last_post_date=last, status=status,
        checked_at=None, selected=True, unfollowed_at=None,
    )


def test_write_names_file_by_timestamp_and_round_trips(tmp_path: Path):
    backups = tmp_path / "backups"
    path = write_backup(backups, [_acct("alice"), _acct("bob", "gone", None)],
                        now=datetime(2026, 9, 14, 18, 30))
    assert path.name == "unfollow-backup-2026-09-14-1830.csv"
    text = path.read_text(encoding="utf-8-sig")
    assert text.splitlines()[0] == "username,profile_url,display_name,last_post_date,status"
    rows = read_backup(path)
    assert [r.username for r in rows] == ["alice", "bob"]
    assert rows[0].profile_url == "https://www.instagram.com/alice/"
    assert rows[0].last_post_date == "2024-05-01"
    assert rows[1].last_post_date == ""
    assert rows[1].status == "gone"


def test_list_backups_newest_first(tmp_path: Path):
    backups = tmp_path / "backups"
    write_backup(backups, [_acct("a")], now=datetime(2026, 1, 1, 9, 0))
    write_backup(backups, [_acct("a")], now=datetime(2026, 3, 1, 9, 0))
    names = [p.name for p in list_backups(backups)]
    assert names == ["unfollow-backup-2026-03-01-0900.csv", "unfollow-backup-2026-01-01-0900.csv"]


def test_list_backups_when_folder_missing(tmp_path: Path):
    assert list_backups(tmp_path / "nope") == []


def test_write_backup_utf8_sig_encoding_with_special_chars(tmp_path: Path):
    backups = tmp_path / "backups"
    acct = Account(
        username="alice", display_name='Zoë "Z", ✨',
        profile_url="https://www.instagram.com/alice/",
        profile_pic_url=None, post_count=1, last_post_date=date(2024, 5, 1),
        status="inactive", checked_at=None, selected=True, unfollowed_at=None,
    )
    path = write_backup(backups, [acct], now=datetime(2026, 9, 14, 18, 30))

    # Verify file starts with UTF-8 BOM
    file_bytes = path.read_bytes()
    assert file_bytes[:3] == b'\xef\xbb\xbf', "File should start with UTF-8 BOM"

    # Verify round-trip preserves username and display name
    rows = read_backup(path)
    assert rows[0].username == "alice", "Username should not include BOM character"
    assert rows[0].display_name == 'Zoë "Z", ✨', "Display name with special chars should be preserved"


def test_write_backup_escapes_formula_injection(tmp_path: Path):
    backups = tmp_path / "backups"
    acct = Account(
        username="alice", display_name='=HYPERLINK("x")',
        profile_url="https://www.instagram.com/alice/",
        profile_pic_url=None, post_count=1, last_post_date=date(2024, 5, 1),
        status="inactive", checked_at=None, selected=True, unfollowed_at=None,
    )
    path = write_backup(backups, [acct], now=datetime(2026, 9, 14, 18, 30))

    with path.open(newline="", encoding="utf-8-sig") as f:
        raw_rows = list(csv.reader(f))
    assert raw_rows[1][2].startswith("'=")

    rows = read_backup(path)
    assert rows[0].display_name == '\'=HYPERLINK("x")'
