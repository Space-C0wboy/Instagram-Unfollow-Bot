import asyncio
from datetime import date, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from igcleanup.driver.types import Gone, Posted
from igcleanup.jobs.engine import MSG_LOGIN, JobRunner
from igcleanup.jobs.progress import ProgressBus
from igcleanup.settings import Settings, save_settings
from igcleanup.storage.accounts import AccountRepo
from igcleanup.storage.db import connect
from igcleanup.storage.jobs import JobRepo
from igcleanup.ui.app import AppContext, create_app, watch_login
from tests.fake_driver import FakeDriver

NOW = datetime(2026, 9, 14, 10, 0)


FAST = Settings(action_delay_seconds=(0, 0), scan_delay_seconds=(0.02, 0.02),
                scan_rest_seconds=(0, 0))


def make_ctx(tmp_path: Path, driver=None, logged_in=True) -> AppContext:
    settings_path = tmp_path / "settings.json"
    save_settings(settings_path, FAST)
    ctx = AppContext(
        conn=connect(":memory:"), driver=driver or FakeDriver(), settings_path=settings_path,
        settings=FAST,
        backups_dir=tmp_path / "backups", runner=JobRunner(), bus=ProgressBus(),
        login_ready=asyncio.Event(), now=lambda: NOW, today=lambda: NOW.date(),
    )
    if logged_in:
        ctx.login_ready.set()
    return ctx


@pytest.fixture
def client_factory(tmp_path):
    def _make(driver=None, logged_in=True):
        ctx = make_ctx(tmp_path, driver, logged_in)
        app = create_app(ctx)
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://t"), ctx
    return _make


async def test_state_before_anything(client_factory):
    client, ctx = client_factory()
    r = await client.get("/api/state")
    body = r.json()
    assert body["logged_in"] is True
    assert body["active_job"] is None
    assert body["latest_scan"] is None
    assert body["account_count"] == 0
    assert body["settings"] == {"daily_action_cap": 100, "inactivity_months": 12, "welcome_seen": False,
                                "rescan_after_days": 30, "speed": "careful"}


async def test_scan_start_runs_to_done_and_review_groups(client_factory):
    driver = FakeDriver(following=[("old", "O"), ("gone", "G")],
                        profiles={"old": Posted(date(2024, 1, 1), 2, "pic"), "gone": Gone()})
    client, ctx = client_factory(driver)
    r = await client.post("/api/scan/start", json={"rescan": False})
    assert r.status_code == 200
    await ctx.runner.wait()
    state = (await client.get("/api/state")).json()
    assert state["latest_scan"]["state"] == "done"
    assert state["targets_remaining"] == 2
    review = (await client.get("/api/review")).json()
    assert [a["username"] for a in review["groups"]["inactive"]] == ["old"]
    assert [a["username"] for a in review["groups"]["gone"]] == ["gone"]
    assert review["groups"]["inactive"][0]["selected"] is True


async def test_scan_refused_when_not_logged_in(client_factory):
    client, _ = client_factory(logged_in=False)
    r = await client.post("/api/scan/start", json={"rescan": False})
    assert (r.status_code, r.json()["error"]) == (409, "not_logged_in")


async def test_selection_and_unfollow_flow(client_factory, tmp_path):
    driver = FakeDriver(following=[("a", "A"), ("b", "B")],
                        profiles={"a": Gone(), "b": Gone()})
    client, ctx = client_factory(driver)
    await client.post("/api/scan/start", json={"rescan": False})
    await ctx.runner.wait()
    await client.post("/api/selection", json={"usernames": ["b"], "selected": False})
    r = await client.post("/api/unfollow/start")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert Path(r.json()["backup"]).exists()
    await ctx.runner.wait()
    assert driver.calls[-1] == ("unfollow", "a")
    review = (await client.get("/api/review")).json()
    assert [a["username"] for a in review["groups"]["gone"]] == ["b"]


async def test_unfollow_with_nothing_selected_is_400(client_factory):
    client, _ = client_factory()
    r = await client.post("/api/unfollow/start")
    assert (r.status_code, r.json()["error"]) == (400, "nothing_selected")


async def test_settings_round_trip_and_validation(client_factory):
    client, ctx = client_factory()
    r = await client.put("/api/settings", json={"daily_action_cap": 50, "inactivity_months": 6})
    assert r.status_code == 200
    got = (await client.get("/api/settings")).json()
    assert {k: got[k] for k in ("daily_action_cap", "inactivity_months", "rescan_after_days", "speed")} == {
        "daily_action_cap": 50, "inactivity_months": 6, "rescan_after_days": 30, "speed": "careful"}
    assert set(got["speed_presets"]) == {"careful", "faster", "fastest"}
    assert ctx.settings.daily_action_cap == 50
    r = await client.put("/api/settings", json={"daily_action_cap": 0, "inactivity_months": 6})
    assert r.status_code == 422


async def test_backups_list_and_restore_start(client_factory, tmp_path):
    client, ctx = client_factory()
    ctx.backups_dir.mkdir()
    (ctx.backups_dir / "unfollow-backup-2026-09-01-0900.csv").write_text(
        "username,profile_url,display_name,last_post_date,status\n"
        "z,https://www.instagram.com/z/,Z,,gone\n", encoding="utf-8")
    assert (await client.get("/api/backups")).json() == [
        {"name": "unfollow-backup-2026-09-01-0900.csv", "count": 1}]
    r = await client.post("/api/restore/start", json={"name": "unfollow-backup-2026-09-01-0900.csv"})
    assert r.status_code == 200
    await ctx.runner.wait()
    assert ctx.driver.calls == [("follow", "z")]
    assert (await client.post("/api/restore/start", json={"name": "nope.csv"})).status_code == 404


async def test_pause_and_resume_rebuild_job(client_factory):
    driver = FakeDriver(following=[("a", "A"), ("b", "B")], profiles={"a": Gone(), "b": Gone()})
    client, ctx = client_factory(driver)
    await client.post("/api/scan/start", json={"rescan": False})
    await client.post("/api/jobs/pause")
    await ctx.runner.wait()
    state = (await client.get("/api/state")).json()
    assert state["active_job"]["state"] == "paused"
    assert state["active_job"]["daily_action_cap"] == 100
    r = await client.post("/api/jobs/resume")
    assert r.status_code == 200
    await ctx.runner.wait()
    assert (await client.get("/api/state")).json()["latest_scan"]["state"] == "done"


async def test_stale_flag_after_30_days(client_factory):
    client, ctx = client_factory()
    job = JobRepo(ctx.conn).create("scan", total=0, started_at=NOW.replace(month=7))
    JobRepo(ctx.conn).update(job.id, state="done", finished_at=NOW.replace(month=7))
    assert (await client.get("/api/state")).json()["stale"] is True


async def test_events_stream_replays_last(client_factory):
    client, ctx = client_factory()
    ctx.bus.publish({"state": "running", "done": 1})
    r = await client.get("/api/events?limit=1")
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text == 'data: {"state": "running", "done": 1, "replay": true}\n\n'


async def test_watch_login_resumes_paused_job(tmp_path):
    driver = FakeDriver(following=[("a", "A")], profiles={"a": Gone()}, logged_in=False)
    ctx = make_ctx(tmp_path, driver)
    job = JobRepo(ctx.conn).create("scan", total=0)
    JobRepo(ctx.conn).update(job.id, state="paused", message=MSG_LOGIN)
    task = asyncio.create_task(watch_login(ctx, interval=0.01))
    await asyncio.sleep(0.05)
    assert JobRepo(ctx.conn).get(job.id).state == "paused"
    driver.logged_in = True
    await asyncio.sleep(0.05)
    await ctx.runner.wait()
    task.cancel()
    assert JobRepo(ctx.conn).get(job.id).state == "done"
    assert AccountRepo(ctx.conn).get("a").status == "gone"


class _FlakyLoginDriver(FakeDriver):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._is_logged_in_calls = 0

    async def is_logged_in(self) -> bool:
        self._is_logged_in_calls += 1
        if self._is_logged_in_calls == 1:
            raise RuntimeError("boom")
        return True


async def test_watch_login_survives_driver_error(tmp_path):
    driver = _FlakyLoginDriver(following=[("a", "A")], profiles={"a": Gone()}, logged_in=False)
    ctx = make_ctx(tmp_path, driver)
    job = JobRepo(ctx.conn).create("scan", total=0)
    JobRepo(ctx.conn).update(job.id, state="paused", message=MSG_LOGIN)
    task = asyncio.create_task(watch_login(ctx, interval=0.01))
    await asyncio.sleep(0.1)
    await ctx.runner.wait()
    task.cancel()
    assert JobRepo(ctx.conn).get(job.id).state == "done"


async def test_selection_validation(client_factory):
    client, _ = client_factory()
    r = await client.post("/api/selection", json={"usernames": "alice", "selected": True})
    assert r.status_code == 422
    assert r.json() == {"error": "invalid"}
    r = await client.post("/api/selection", json={"usernames": ["a"]})
    assert r.status_code == 422
    assert r.json() == {"error": "invalid"}


async def test_last_failed_in_state(client_factory):
    client, ctx = client_factory()
    job = JobRepo(ctx.conn).create("scan", total=1, started_at=NOW)
    JobRepo(ctx.conn).update(job.id, state="failed", message="boom", finished_at=NOW)
    state = (await client.get("/api/state")).json()
    assert state["last_failed"]["message"] == "boom"


async def test_cross_origin_post_is_forbidden(client_factory):
    client, _ = client_factory()
    r = await client.post("/api/jobs/pause", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden"}


async def test_scan_start_refuses_while_other_job_paused(client_factory):
    client, ctx = client_factory()
    job = JobRepo(ctx.conn).create("unfollow", total=1, started_at=NOW)
    JobRepo(ctx.conn).update(job.id, state="paused")
    r = await client.post("/api/scan/start", json={"rescan": False})
    assert (r.status_code, r.json()["error"]) == (409, "job_paused")
    r = await client.post("/api/scan/start", json={"rescan": True})
    assert r.status_code == 200
    assert JobRepo(ctx.conn).get(job.id).state == "failed"


async def test_backup_rows_and_selective_restore(client_factory):
    client, ctx = client_factory()
    ctx.backups_dir.mkdir()
    (ctx.backups_dir / "unfollow-backup-2026-09-01-0900.csv").write_text(
        "\n".join([
            "username,profile_url,display_name,last_post_date,status",
            "a,https://www.instagram.com/a/,A,2024-01-01,inactive",
            "b,https://www.instagram.com/b/,B,,gone",
            "c,https://www.instagram.com/c/,C,2024-01-01,inactive",
        ]) + "\n", encoding="utf-8")
    rows = (await client.get("/api/backups/unfollow-backup-2026-09-01-0900.csv")).json()["rows"]
    assert [r["username"] for r in rows] == ["a", "b", "c"]
    assert rows[1]["status"] == "gone"
    assert (await client.get("/api/backups/nope.csv")).status_code == 404

    r = await client.post("/api/restore/start",
                          json={"name": "unfollow-backup-2026-09-01-0900.csv", "usernames": ["c", "a"]})
    assert r.status_code == 200 and r.json()["total"] == 2
    await ctx.runner.wait()
    assert ctx.driver.calls == [("follow", "a"), ("follow", "c")]
    # the selection file is not listed as a backup
    names = [b["name"] for b in (await client.get("/api/backups")).json()]
    assert names == ["unfollow-backup-2026-09-01-0900.csv"]

    r = await client.post("/api/restore/start",
                          json={"name": "unfollow-backup-2026-09-01-0900.csv", "usernames": ["zzz"]})
    assert (r.status_code, r.json()["error"]) == (400, "nothing_selected")
    r = await client.post("/api/restore/start",
                          json={"name": "unfollow-backup-2026-09-01-0900.csv", "usernames": "a"})
    assert r.status_code == 422


async def test_avatars_are_served(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.avatars_dir = tmp_path / "avatars"
    app = create_app(ctx)
    (ctx.avatars_dir / "someone.jpg").write_bytes(b"jpeg")
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    r = await client.get("/avatars/someone.jpg")
    assert r.status_code == 200 and r.content == b"jpeg"


async def test_keep_routes_and_review_group(client_factory):
    driver = FakeDriver(following=[("a", "A"), ("b", "B")], profiles={"a": Gone(), "b": Gone()})
    client, ctx = client_factory(driver)
    await client.post("/api/scan/start", json={"rescan": False})
    await ctx.runner.wait()
    assert (await client.post("/api/keep", json={"username": "a", "kept": True})).status_code == 200
    review = (await client.get("/api/review")).json()
    assert [x["username"] for x in review["groups"]["kept"]] == ["a"]
    assert [x["username"] for x in review["groups"]["gone"]] == ["b"]
    assert (await client.get("/api/state")).json()["targets_remaining"] == 1
    assert (await client.post("/api/keep", json={"username": "a", "kept": "yes"})).status_code == 422
    await client.post("/api/keep", json={"username": "a", "kept": False})
    assert (await client.get("/api/state")).json()["targets_remaining"] == 2


async def test_job_summary_and_welcome_flag(client_factory):
    driver = FakeDriver(following=[("a", "A"), ("b", "B")],
                        profiles={"a": Gone(), "b": Posted(date(2024, 1, 1), 1, None)})
    client, ctx = client_factory(driver)
    assert (await client.get("/api/state")).json()["settings"]["welcome_seen"] is False
    await client.post("/api/welcome/seen")
    assert (await client.get("/api/state")).json()["settings"]["welcome_seen"] is True
    r = await client.post("/api/scan/start", json={"rescan": False})
    await ctx.runner.wait()
    summary = (await client.get(f"/api/jobs/{r.json()['job_id']}/summary")).json()
    assert summary["state"] == "done"
    assert summary["counts"] == {"gone": 1, "inactive": 1}
    assert (await client.get("/api/jobs/999/summary")).status_code == 404


async def test_unfollow_and_restore_refused_while_a_job_is_paused(client_factory):
    client, ctx = client_factory()
    ctx.backups_dir.mkdir()
    (ctx.backups_dir / "unfollow-backup-2026-09-01-0900.csv").write_text(
        "username,profile_url,display_name,last_post_date,status\nz,https://www.instagram.com/z/,Z,,gone\n".replace("\\n", "\n"),
        encoding="utf-8")
    paused = JobRepo(ctx.conn).create("unfollow", total=3)
    JobRepo(ctx.conn).update(paused.id, state="paused")
    r = await client.post("/api/unfollow/start")
    assert (r.status_code, r.json()["error"]) == (409, "job_paused")
    r = await client.post("/api/restore/start", json={"name": "unfollow-backup-2026-09-01-0900.csv"})
    assert (r.status_code, r.json()["error"]) == (409, "job_paused")
    r = await client.post("/api/jobs/stop")
    assert r.status_code == 200
    j = JobRepo(ctx.conn).get(paused.id)
    assert (j.state, j.message) == ("done", "Stopped early.")
    assert (await client.post("/api/jobs/stop")).status_code == 404


async def test_start_over_forgets_the_owner(client_factory):
    driver = FakeDriver(following=[("a", "A")], profiles={"a": Gone()}, owner="owner_one")
    client, ctx = client_factory(driver)
    await client.post("/api/scan/start", json={"rescan": False})
    await ctx.runner.wait()
    from igcleanup.storage.meta import MetaRepo
    assert MetaRepo(ctx.conn).get("owner") == "owner_one"
    driver.owner = "other"
    await client.post("/api/scan/start", json={"rescan": True})
    await ctx.runner.wait()
    # Scan again keeps the account: a different login is refused, not silently adopted.
    assert MetaRepo(ctx.conn).get("owner") == "owner_one"
    assert (await client.get("/api/state")).json()["latest_scan"]["state"] == "failed"
    await client.post("/api/reset")
    assert MetaRepo(ctx.conn).get("owner") is None
    await client.post("/api/scan/start", json={"rescan": False})
    await ctx.runner.wait()
    assert MetaRepo(ctx.conn).get("owner") == "other"


async def test_stopping_a_scan_marks_it_failed_not_done(client_factory):
    client, ctx = client_factory()
    scan = JobRepo(ctx.conn).create("scan", total=30)
    JobRepo(ctx.conn).update(scan.id, state="paused", done=7)
    assert (await client.post("/api/jobs/stop")).status_code == 200
    j = JobRepo(ctx.conn).get(scan.id)
    assert (j.state, j.message) == ("failed", "Stopped early.")
    state = (await client.get("/api/state")).json()
    assert state["latest_scan"]["state"] == "failed"


async def test_rescan_is_incremental_and_reset_starts_over(client_factory):
    driver = FakeDriver(following=[("a", "A"), ("b", "B")], profiles={"a": Gone(), "b": Gone()}, owner="owner_one")
    client, ctx = client_factory(driver)
    await client.post("/api/scan/start", json={"rescan": False})
    await ctx.runner.wait()
    await client.post("/api/keep", json={"username": "a", "kept": True})
    r = await client.post("/api/scan/start", json={"rescan": True})
    await ctx.runner.wait()
    from igcleanup.storage.meta import MetaRepo
    assert JobRepo(ctx.conn).get(r.json()["job_id"]).source == "refresh"
    assert MetaRepo(ctx.conn).get("owner") == "owner_one"  # a rescan keeps the account
    assert AccountRepo(ctx.conn).kept_usernames() == {"a"}
    assert [c for c in driver.calls if c[0] == "inspect"] == [("inspect", "a"), ("inspect", "b")]  # nothing was stale
    state = (await client.get("/api/state")).json()
    assert state["unchecked"] == 0 and state["settings"]["rescan_after_days"] == 30

    assert (await client.post("/api/reset")).status_code == 200
    assert AccountRepo(ctx.conn).count() == 0
    assert AccountRepo(ctx.conn).kept_usernames() == set()
    assert MetaRepo(ctx.conn).get("owner") is None


async def test_settings_rescan_days_round_trip(client_factory):
    client, ctx = client_factory()
    r = await client.put("/api/settings", json={"daily_action_cap": 100, "inactivity_months": 12, "rescan_after_days": 7})
    assert r.status_code == 200 and r.json()["rescan_after_days"] == 7
    assert ctx.settings.rescan_after_days == 7
    r = await client.put("/api/settings", json={"daily_action_cap": 100, "inactivity_months": 12, "rescan_after_days": 0})
    assert r.status_code == 422


async def test_state_reports_unchecked_and_review_shows_checked_date(client_factory):
    driver = FakeDriver(following=[("a", "A"), ("b", "B")], profiles={"a": Gone(), "b": Gone()})
    client, ctx = client_factory(driver)
    await client.post("/api/scan/start", json={"rescan": False})
    await client.post("/api/jobs/pause")
    await ctx.runner.wait()
    state = (await client.get("/api/state")).json()
    assert state["unchecked"] >= 1
    review = (await client.get("/api/review")).json()
    assert review["unchecked"] == state["unchecked"]
    await client.post("/api/jobs/resume")
    await ctx.runner.wait()
    review = (await client.get("/api/review")).json()
    assert review["groups"]["gone"][0]["checked_at"] == "2026-09-14"


async def test_settings_speed_and_quit(client_factory):
    client, ctx = client_factory()
    closed = []
    ctx.on_quit = lambda: closed.append(True)
    r = await client.put("/api/settings", json={"daily_action_cap": 100, "inactivity_months": 12,
                                                "rescan_after_days": 30, "speed": "faster"})
    assert r.status_code == 200 and r.json()["speed"] == "faster"
    assert ctx.settings.action_delay_seconds == (15, 45)
    assert "careful" in r.json()["speed_presets"]
    r = await client.put("/api/settings", json={"daily_action_cap": 100, "inactivity_months": 12,
                                                "rescan_after_days": 30, "speed": "warp"})
    assert r.status_code == 422
    assert (await client.post("/api/quit")).status_code == 200 and closed == [True]
    assert (await client.post("/api/open_terms")).status_code == 200
    assert ctx.driver.calls[-1][0] == "open_url"
