from datetime import datetime
from pathlib import Path

from igcleanup.driver.types import ActionFailed, PageLoadError, ParseError
from igcleanup.jobs.engine import MSG_CONNECTION, MSG_DONE_TODAY
from igcleanup.jobs.pacing import Pacer
from igcleanup.jobs.progress import ProgressBus
from igcleanup.jobs.restore import RestoreJob, prepare_restore
from igcleanup.settings import Settings
from igcleanup.storage.db import connect
from igcleanup.storage.jobs import EventRepo, JobRepo
from tests.fake_driver import FakeDriver

NOW = datetime(2026, 9, 14, 10, 0)
CSV = (
    "username,profile_url,display_name,last_post_date,status\n"
    "a,https://www.instagram.com/a/,A,2024-01-01,inactive\n"
    "b,https://www.instagram.com/b/,B,,gone\n"
    "c,https://www.instagram.com/c/,C,2024-01-01,inactive\n"
)


async def no_sleep(_):
    pass


def make(conn, driver, job_id, path, settings=Settings(), now=NOW):
    return RestoreJob(job_id, conn, driver, settings, Pacer(settings, sleep=no_sleep),
                      ProgressBus(), now=lambda: now, backup_path=path,
                      today=lambda: now.date())


def write_csv(tmp_path: Path) -> Path:
    p = tmp_path / "unfollow-backup-2026-09-01-0900.csv"
    p.write_text(CSV, encoding="utf-8")
    return p


async def test_restore_follows_every_row_and_records_events(tmp_path: Path):
    conn = connect(":memory:")
    path = write_csv(tmp_path)
    job = prepare_restore(conn, path, now=lambda: NOW)
    assert (job.type, job.total, job.source) == ("restore", 3, str(path))
    driver = FakeDriver()
    await make(conn, driver, job.id, path).run()
    assert driver.calls == [("follow", "a"), ("follow", "b"), ("follow", "c")]
    j = JobRepo(conn).get(job.id)
    assert (j.state, j.done, j.message) == ("done", 3, "Restored 3 accounts.")
    assert EventRepo(conn).count_on(NOW.date()) == 3


async def test_restore_respects_cap_and_resumes_without_repeating(tmp_path: Path):
    conn = connect(":memory:")
    path = write_csv(tmp_path)
    job = prepare_restore(conn, path, now=lambda: NOW)
    driver = FakeDriver()
    settings = Settings(daily_action_cap=2)
    await make(conn, driver, job.id, path, settings).run()
    j = JobRepo(conn).get(job.id)
    assert (j.state, j.message, j.done) == ("paused", MSG_DONE_TODAY, 2)
    await make(conn, driver, job.id, path, settings, now=NOW.replace(day=15)).run()
    assert JobRepo(conn).get(job.id).state == "done"
    assert driver.calls == [("follow", "a"), ("follow", "b"), ("follow", "c")]


async def test_restore_reports_failures_in_message(tmp_path: Path):
    conn = connect(":memory:")
    path = write_csv(tmp_path)
    job = prepare_restore(conn, path, now=lambda: NOW)
    driver = FakeDriver(follow_errors={"b": [ParseError(), ParseError()],
                                       "c": [ActionFailed()]})
    await make(conn, driver, job.id, path).run()
    j = JobRepo(conn).get(job.id)
    assert (j.state, j.done) == ("done", 3)
    assert j.message == "Restored 2 accounts. Could not re-follow: b"
    assert EventRepo(conn).count_on(NOW.date()) == 2


async def test_single_page_load_error_leaves_restore_paused_for_retry(tmp_path: Path):
    conn = connect(":memory:")
    path = write_csv(tmp_path)
    job = prepare_restore(conn, path, now=lambda: NOW)
    driver = FakeDriver(follow_errors={"b": [PageLoadError()]})
    await make(conn, driver, job.id, path).run()
    j = JobRepo(conn).get(job.id)
    assert (j.state, j.message) == ("paused", MSG_CONNECTION)
    assert EventRepo(conn).usernames_for_job(job.id) == {"a", "c"}

    await make(conn, driver, job.id, path).run()
    j = JobRepo(conn).get(job.id)
    assert (j.state, j.message) == ("done", "Restored 3 accounts.")
    assert driver.calls == [
        ("follow", "a"), ("follow", "b"), ("follow", "c"), ("follow", "b"),
    ]


async def test_prepare_restore_with_selection_writes_selection_file(tmp_path: Path):
    conn = connect(":memory:")
    path = write_csv(tmp_path)
    job = prepare_restore(conn, path, now=lambda: NOW, usernames=["b"])
    assert job.total == 1
    assert job.source.endswith("restore-selection-2026-09-14-100000.csv")
    driver = FakeDriver()
    await make(conn, driver, job.id, Path(job.source)).run()
    assert driver.calls == [("follow", "b")]
