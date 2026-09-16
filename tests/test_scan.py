from datetime import date

from igcleanup.driver.types import (Gone, LoggedOut, NeverPosted, PageLoadError, ParseError,
                                    Posted, RateLimited)
from igcleanup.jobs.engine import MSG_CONNECTION, MSG_LAYOUT, MSG_LOGIN, MSG_RATE_LIMITED
from igcleanup.jobs.pacing import Pacer
from igcleanup.jobs.progress import ProgressBus
from igcleanup.jobs.scan import ScanJob
from igcleanup.settings import Settings
from igcleanup.storage.accounts import AccountRepo
from igcleanup.storage.db import connect
from igcleanup.storage.jobs import JobRepo
from tests.fake_driver import FakeDriver

TODAY = date(2026, 9, 14)


async def no_sleep(_):
    pass


def scan_job(conn, driver, job_id=None):
    jobs = JobRepo(conn)
    job_id = job_id or jobs.create("scan", total=0).id
    return ScanJob(job_id, conn, driver, Settings(), Pacer(Settings(), sleep=no_sleep),
                   ProgressBus(), today=lambda: TODAY)


def statuses(conn):
    return {a.username: a.status for a in AccountRepo(conn).all()}


async def test_full_scan_classifies_and_applies_default_selection():
    conn = connect(":memory:")
    driver = FakeDriver(
        following=[("gone", "G"), ("never", "N"), ("old", "O"), ("new", "W")],
        profiles={
            "gone": Gone(),
            "never": NeverPosted(profile_pic_url="p"),
            "old": Posted(date(2025, 9, 1), 5, "p"),
            "new": Posted(date(2025, 10, 1), 5, "p"),
        },
    )
    job = scan_job(conn, driver)
    await job.run()
    assert statuses(conn) == {"gone": "gone", "never": "never_posted", "old": "inactive", "new": "active"}
    j = JobRepo(conn).get(job.job_id)
    assert (j.state, j.done, j.total) == ("done", 4, 4)
    assert {a.username for a in AccountRepo(conn).targets()} == {"gone", "old"}
    assert AccountRepo(conn).get("old").post_count == 5


async def test_posted_result_updates_display_name():
    conn = connect(":memory:")
    driver = FakeDriver(
        following=[("alice", "Alice Original")],
        profiles={"alice": Posted(date(2024, 1, 1), 5, "p", display_name="Old Name")},
    )
    job = scan_job(conn, driver)
    await job.run()
    assert AccountRepo(conn).get("alice").display_name == "Old Name"


async def test_rate_limit_pauses_and_rerun_resumes_from_pending():
    conn = connect(":memory:")
    driver = FakeDriver(
        following=[("a", "A"), ("b", "B")],
        profiles={"a": Gone(), "b": [RateLimited(), Gone()]},
    )
    job = scan_job(conn, driver)
    await job.run()
    j = JobRepo(conn).get(job.job_id)
    assert (j.state, j.message, j.done) == ("paused", MSG_RATE_LIMITED, 1)
    assert statuses(conn) == {"a": "gone", "b": "pending"}

    again = scan_job(conn, driver, job_id=job.job_id)
    await again.run()
    assert JobRepo(conn).get(job.job_id).state == "done"
    assert statuses(conn) == {"a": "gone", "b": "gone"}
    assert driver.calls.count(("list_following", "")) == 1


async def test_five_consecutive_parse_errors_pause_with_layout_message():
    conn = connect(":memory:")
    names = [f"u{i}" for i in range(6)]
    driver = FakeDriver(following=[(n, n) for n in names],
                        profiles={n: ParseError() for n in names})
    job = scan_job(conn, driver)
    await job.run()
    j = JobRepo(conn).get(job.job_id)
    assert (j.state, j.message) == ("paused", MSG_LAYOUT)
    assert j.done == 5
    s = statuses(conn)
    assert [s[n] for n in names] == ["error"] * 5 + ["pending"]


async def test_parse_errors_reset_after_a_success():
    conn = connect(":memory:")
    names = [f"u{i}" for i in range(9)]
    profiles = {n: ParseError() for n in names}
    profiles["u4"] = Gone()
    driver = FakeDriver(following=[(n, n) for n in names], profiles=profiles)
    job = scan_job(conn, driver)
    await job.run()
    assert JobRepo(conn).get(job.job_id).state == "done"
    assert statuses(conn)["u8"] == "error"


async def test_three_page_load_errors_pause_with_connection_message():
    conn = connect(":memory:")
    names = ["a", "b", "c", "d"]
    driver = FakeDriver(following=[(n, n) for n in names],
                        profiles={n: PageLoadError() for n in names})
    job = scan_job(conn, driver)
    await job.run()
    j = JobRepo(conn).get(job.job_id)
    assert (j.state, j.message, j.done) == ("paused", MSG_CONNECTION, 0)
    assert set(statuses(conn).values()) == {"pending"}


async def test_logged_out_pauses_and_brings_browser_forward():
    conn = connect(":memory:")
    driver = FakeDriver(following=[("a", "A")], profiles={"a": LoggedOut()})
    job = scan_job(conn, driver)
    await job.run()
    assert JobRepo(conn).get(job.job_id).message == MSG_LOGIN
    assert driver.front_calls == 1


async def test_user_pause_between_profiles():
    conn = connect(":memory:")
    driver = FakeDriver(following=[("a", "A"), ("b", "B")], profiles={"a": Gone(), "b": Gone()})
    job = scan_job(conn, driver)
    original = job.pacer.after_profile

    async def pause_after_first(index):
        job.request_pause()
        await original(index)

    job.pacer.after_profile = pause_after_first
    await job.run()
    j = JobRepo(conn).get(job.job_id)
    assert (j.state, j.done) == ("paused", 1)
    assert statuses(conn) == {"a": "gone", "b": "pending"}


async def test_single_page_load_error_leaves_scan_paused_for_retry():
    conn = connect(":memory:")
    driver = FakeDriver(
        following=[("a", "A"), ("b", "B"), ("c", "C")],
        profiles={"a": Gone(), "b": [PageLoadError(), Gone()], "c": Gone()},
    )
    job = scan_job(conn, driver)
    await job.run()
    assert statuses(conn) == {"a": "gone", "b": "pending", "c": "gone"}
    j = JobRepo(conn).get(job.job_id)
    assert (j.state, j.message) == ("paused", MSG_CONNECTION)

    again = scan_job(conn, driver, job_id=job.job_id)
    await again.run()
    assert JobRepo(conn).get(job.job_id).state == "done"
    assert statuses(conn)["b"] == "gone"


async def test_scan_caches_avatars_locally(tmp_path):
    conn = connect(":memory:")
    driver = FakeDriver(
        following=[("a", "A"), ("b", "B")],
        profiles={"a": Posted(date(2024, 1, 1), 5, "http://cdn/a.jpg"),
                  "b": Posted(date(2024, 1, 1), 5, "http://cdn/b.jpg")},
        images={"http://cdn/a.jpg": b"img-a"},
    )
    jobs = JobRepo(conn)
    job = ScanJob(jobs.create("scan", total=0).id, conn, driver, Settings(),
                  Pacer(Settings(), sleep=no_sleep), ProgressBus(), today=lambda: TODAY,
                  avatars_dir=tmp_path / "avatars")
    await job.run()
    repo = AccountRepo(conn)
    assert repo.get("a").profile_pic_url == "/avatars/a.jpg"
    assert (tmp_path / "avatars" / "a.jpg").read_bytes() == b"img-a"
    assert repo.get("b").profile_pic_url == "http://cdn/b.jpg"  # download failed: keep the link


async def test_following_warning_is_kept_on_the_finished_scan():
    conn = connect(":memory:")
    driver = FakeDriver(following=[("a", "A")], profiles={"a": Gone()})
    driver.following_warning = "Only 1 of 40 accounts could be listed."
    job = scan_job(conn, driver)
    await job.run()
    j = JobRepo(conn).get(job.job_id)
    assert (j.state, j.message) == ("done", "Only 1 of 40 accounts could be listed.")


async def test_refresh_scan_keeps_recent_rows_and_rechecks_stale_ones():
    from datetime import datetime
    conn = connect(":memory:")
    repo = AccountRepo(conn)
    now = datetime(2026, 9, 15, 12, 0)
    repo.upsert_following([("recent", "R"), ("stale", "S"), ("unfollowed_by_her", "U")])
    repo.set_result("recent", status="inactive", last_post_date=date(2024, 1, 1), checked_at=now.replace(day=10))
    repo.set_result("stale", status="active", last_post_date=date(2026, 8, 1), checked_at=now.replace(month=6))
    repo.set_result("unfollowed_by_her", status="inactive", checked_at=now.replace(day=10))
    repo.apply_default_selection()
    repo.set_selected(["recent"], False)  # the user unticked it earlier

    driver = FakeDriver(
        following=[("recent", "R"), ("stale", "S"), ("newbie", "N")],
        profiles={"stale": Posted(date(2024, 2, 2), 3, "p"), "newbie": Gone()},
    )
    jobs = JobRepo(conn)
    job_row = jobs.create("scan", total=3, source="refresh")
    job = ScanJob(job_row.id, conn, driver, Settings(rescan_after_days=30),
                  Pacer(Settings(), sleep=no_sleep), ProgressBus(), now=lambda: now,
                  today=lambda: now.date(), refresh=True)
    await job.run()

    assert [c for c in driver.calls if c[0] == "inspect"] == [("inspect", "newbie"), ("inspect", "stale")]
    assert repo.get("unfollowed_by_her") is None
    assert repo.get("recent").selected is False and repo.get("recent").status == "inactive"
    assert repo.get("stale").status == "inactive" and repo.get("stale").selected is True
    assert repo.get("newbie").status == "gone" and repo.get("newbie").selected is True
    assert jobs.get(job_row.id).state == "done"
