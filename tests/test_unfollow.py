from datetime import date, datetime
from pathlib import Path

import pytest

from igcleanup.driver.types import ActionFailed, PageLoadError, ParseError
from igcleanup.jobs.engine import MSG_CONNECTION, MSG_DONE_TODAY
from igcleanup.jobs.pacing import Pacer
from igcleanup.jobs.progress import ProgressBus
from igcleanup.jobs.unfollow import UnfollowJob, prepare_unfollow
from igcleanup.settings import Settings
from igcleanup.storage.accounts import AccountRepo
from igcleanup.storage.backup import read_backup
from igcleanup.storage.db import connect
from igcleanup.storage.jobs import EventRepo, JobRepo
from tests.fake_driver import FakeDriver

NOW = datetime(2026, 9, 14, 10, 0)


async def no_sleep(_):
    pass


def seed(conn, usernames):
    repo = AccountRepo(conn)
    repo.upsert_following([(u, u.upper()) for u in usernames])
    for u in usernames:
        repo.set_result(u, status="inactive", last_post_date=date(2024, 1, 1), checked_at=NOW)
    repo.apply_default_selection()
    return repo


def make_job(conn, driver, job_id, settings=Settings(), now=NOW):
    return UnfollowJob(job_id, conn, driver, settings, Pacer(settings, sleep=no_sleep),
                       ProgressBus(), now=lambda: now, today=lambda: now.date())


async def test_prepare_writes_backup_then_job_and_run_unfollows_all(tmp_path: Path):
    conn = connect(":memory:")
    seed(conn, ["a", "b"])
    job, path = prepare_unfollow(conn, tmp_path / "backups", now=lambda: NOW)
    assert path.name == "unfollow-backup-2026-09-14-1000.csv"
    assert [r.username for r in read_backup(path)] == ["a", "b"]
    assert (job.type, job.total) == ("unfollow", 2)

    driver = FakeDriver()
    await make_job(conn, driver, job.id).run()
    assert driver.calls == [("unfollow", "a"), ("unfollow", "b")]
    j = JobRepo(conn).get(job.id)
    assert (j.state, j.done) == ("done", 2)
    assert AccountRepo(conn).targets() == []
    assert EventRepo(conn).count_on(NOW.date()) == 2
    assert AccountRepo(conn).get("a").unfollowed_at == NOW


async def test_prepare_with_nothing_selected_raises(tmp_path: Path):
    conn = connect(":memory:")
    with pytest.raises(ValueError):
        prepare_unfollow(conn, tmp_path / "backups", now=lambda: NOW)
    assert not (tmp_path / "backups").exists()


async def test_daily_cap_pauses_and_next_day_continues(tmp_path: Path):
    conn = connect(":memory:")
    seed(conn, ["a", "b", "c"])
    job, _ = prepare_unfollow(conn, tmp_path / "backups", now=lambda: NOW)
    settings = Settings(daily_action_cap=2)
    driver = FakeDriver()
    await make_job(conn, driver, job.id, settings).run()
    j = JobRepo(conn).get(job.id)
    assert (j.state, j.message, j.done) == ("paused", MSG_DONE_TODAY, 2)
    assert [a.username for a in AccountRepo(conn).targets()] == ["c"]

    tomorrow = NOW.replace(day=15)
    await make_job(conn, driver, job.id, settings, now=tomorrow).run()
    assert JobRepo(conn).get(job.id).state == "done"
    assert driver.calls == [("unfollow", "a"), ("unfollow", "b"), ("unfollow", "c")]


async def test_cap_counts_events_from_earlier_jobs_today(tmp_path: Path):
    conn = connect(":memory:")
    seed(conn, ["a"])
    earlier = JobRepo(conn).create("unfollow", total=1)
    EventRepo(conn).record(earlier.id, "zzz", "unfollow", NOW.replace(hour=1))
    job, _ = prepare_unfollow(conn, tmp_path / "backups", now=lambda: NOW)
    driver = FakeDriver()
    await make_job(conn, driver, job.id, Settings(daily_action_cap=1)).run()
    assert JobRepo(conn).get(job.id).message == MSG_DONE_TODAY
    assert driver.calls == []


async def test_action_failed_retries_once_then_marks_error(tmp_path: Path):
    conn = connect(":memory:")
    seed(conn, ["a", "b", "c"])
    job, _ = prepare_unfollow(conn, tmp_path / "backups", now=lambda: NOW)
    driver = FakeDriver(unfollow_errors={
        "a": [ActionFailed(), ActionFailed()],
        "b": [ActionFailed()],
    })
    await make_job(conn, driver, job.id).run()
    assert driver.calls == [("unfollow", "a"), ("unfollow", "a"), ("unfollow", "b"),
                            ("unfollow", "b"), ("unfollow", "c")]
    repo = AccountRepo(conn)
    assert repo.get("a").status == "error"
    assert repo.get("a").selected is False
    assert repo.get("a").unfollowed_at is None
    assert repo.get("b").unfollowed_at == NOW
    j = JobRepo(conn).get(job.id)
    assert (j.state, j.done) == ("done", 3)
    assert EventRepo(conn).count_on(NOW.date()) == 2


async def test_parse_error_retries_once_then_marks_error(tmp_path: Path):
    conn = connect(":memory:")
    seed(conn, ["a", "b"])
    job, _ = prepare_unfollow(conn, tmp_path / "backups", now=lambda: NOW)
    driver = FakeDriver(unfollow_errors={"a": [ParseError(), ParseError()]})
    await make_job(conn, driver, job.id).run()
    assert driver.calls == [("unfollow", "a"), ("unfollow", "a"), ("unfollow", "b")]
    repo = AccountRepo(conn)
    assert repo.get("a").status == "error"
    assert repo.get("a").selected is False
    assert repo.get("a").unfollowed_at is None
    assert repo.get("b").unfollowed_at == NOW
    j = JobRepo(conn).get(job.id)
    assert (j.state, j.done) == ("done", 2)


async def test_three_page_load_errors_pause(tmp_path: Path):
    conn = connect(":memory:")
    seed(conn, ["a", "b", "c", "d"])
    job, _ = prepare_unfollow(conn, tmp_path / "backups", now=lambda: NOW)
    driver = FakeDriver(unfollow_errors={u: [PageLoadError()] for u in "abcd"})
    await make_job(conn, driver, job.id).run()
    j = JobRepo(conn).get(job.id)
    assert (j.state, j.message) == ("paused", MSG_CONNECTION)
    assert len(AccountRepo(conn).targets()) == 4


async def test_single_page_load_error_leaves_job_paused_for_retry(tmp_path: Path):
    conn = connect(":memory:")
    seed(conn, ["a", "b", "c"])
    job, _ = prepare_unfollow(conn, tmp_path / "backups", now=lambda: NOW)
    driver = FakeDriver(unfollow_errors={"b": [PageLoadError()]})
    await make_job(conn, driver, job.id).run()
    repo = AccountRepo(conn)
    assert repo.get("a").unfollowed_at == NOW
    assert repo.get("c").unfollowed_at == NOW
    assert repo.get("b").unfollowed_at is None
    assert repo.get("b").selected is True
    assert [a.username for a in repo.targets()] == ["b"]
    j = JobRepo(conn).get(job.id)
    assert (j.state, j.message) == ("paused", MSG_CONNECTION)

    await make_job(conn, driver, job.id).run()
    assert JobRepo(conn).get(job.id).state == "done"
    assert AccountRepo(conn).get("b").unfollowed_at == NOW


class _DeselectsB(FakeDriver):
    def __init__(self, repo, **kw):
        super().__init__(**kw)
        self.repo = repo

    async def unfollow(self, username):
        await super().unfollow(username)
        if username == "a":
            self.repo.set_selected(["b"], False)


async def test_unticking_mid_run_skips_that_account(tmp_path: Path):
    conn = connect(":memory:")
    repo = seed(conn, ["a", "b", "c"])
    job, _ = prepare_unfollow(conn, tmp_path / "backups", now=lambda: NOW)
    driver = _DeselectsB(repo)
    await make_job(conn, driver, job.id).run()
    assert driver.calls == [("unfollow", "a"), ("unfollow", "c")]
    assert JobRepo(conn).get(job.id).state == "done"
    assert repo.get("b").unfollowed_at is None


class _LowersCap(FakeDriver):
    def __init__(self, holder, **kw):
        super().__init__(**kw)
        self.holder = holder

    async def unfollow(self, username):
        await super().unfollow(username)
        from dataclasses import replace
        self.holder["s"] = replace(self.holder["s"], daily_action_cap=1)


async def test_settings_saved_mid_run_apply_at_next_check(tmp_path: Path):
    conn = connect(":memory:")
    seed(conn, ["a", "b", "c"])
    job, _ = prepare_unfollow(conn, tmp_path / "backups", now=lambda: NOW)
    holder = {"s": Settings(daily_action_cap=50)}
    driver = _LowersCap(holder)
    j = UnfollowJob(job.id, conn, driver, lambda: holder["s"], Pacer(lambda: holder["s"], sleep=no_sleep),
                    ProgressBus(), now=lambda: NOW, today=lambda: NOW.date())
    await j.run()
    assert driver.calls == [("unfollow", "a")]
    assert JobRepo(conn).get(job.id).message == MSG_DONE_TODAY


async def test_wrong_account_fails_with_clear_message(tmp_path: Path):
    conn = connect(":memory:")
    seed(conn, ["a"])
    job, _ = prepare_unfollow(conn, tmp_path / "backups", now=lambda: NOW)
    await make_job(conn, FakeDriver(owner="owner_one"), job.id).run()  # first run records the owner
    assert JobRepo(conn).get(job.id).state == "done"
    seed(conn, ["b"])
    job2, _ = prepare_unfollow(conn, tmp_path / "backups", now=lambda: NOW)
    driver = FakeDriver(owner="someone_else")
    await make_job(conn, driver, job2.id).run()
    j = JobRepo(conn).get(job2.id)
    assert j.state == "failed" and "@someone_else" in j.message and "@owner_one" in j.message
    assert driver.calls == []


async def test_activity_log_lines(tmp_path: Path):
    conn = connect(":memory:")
    seed(conn, ["a", "b"])
    job, _ = prepare_unfollow(conn, tmp_path / "backups", now=lambda: NOW)
    driver = FakeDriver(unfollow_errors={"b": [ActionFailed(), ActionFailed()]})
    log = tmp_path / "activity.log"
    j = UnfollowJob(job.id, conn, driver, Settings(), Pacer(Settings(), sleep=no_sleep), ProgressBus(),
                    now=lambda: NOW, today=lambda: NOW.date(), activity_log=log)
    await j.run()
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("2026-09-14 10:00:00  Unfollowing started")
    assert "2026-09-14 10:00:00  unfollowed @a" in lines
    assert "2026-09-14 10:00:00  could not unfollow @b" in lines
    assert lines[-1] == "2026-09-14 10:00:00  Unfollowing done"
