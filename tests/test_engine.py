import asyncio
from datetime import date

import pytest

from igcleanup.driver.types import LoggedOut, RateLimited
from igcleanup.jobs.engine import (MSG_LOGIN, MSG_RATE_LIMITED, BaseJob, JobRunner,
                                   months_ago)
from igcleanup.jobs.pacing import Pacer
from igcleanup.jobs.progress import ProgressBus
from igcleanup.settings import Settings
from igcleanup.storage.db import connect
from igcleanup.storage.jobs import JobRepo
from tests.fake_driver import FakeDriver


async def no_sleep(_):
    pass


def make(job_cls, conn, driver=None):
    job = JobRepo(conn).create("scan", total=0)
    return job_cls(job.id, conn, driver or FakeDriver(), Settings(),
                   Pacer(Settings(), sleep=no_sleep), ProgressBus())


class Finishes(BaseJob):
    async def _run(self):
        self.set_state("done", done=1, total=1)


class Raises(BaseJob):
    exc = RuntimeError("boom")

    async def _run(self):
        raise self.exc


class PausesItself(BaseJob):
    async def _run(self):
        for i in range(3):
            if self.check_pause():
                return
            self.set_state("running", done=i + 1)


def test_months_ago():
    assert months_ago(date(2026, 9, 14), 12) == date(2025, 9, 14)
    assert months_ago(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert months_ago(date(2026, 1, 15), 13) == date(2024, 12, 15)


async def test_run_persists_done_and_publishes():
    conn = connect(":memory:")
    job = make(Finishes, conn)
    await job.run()
    assert JobRepo(conn).get(job.job_id).state == "done"
    assert job.bus.last["state"] == "done"
    assert job.bus.last["done"] == 1
    assert job.bus.last["actions_today"] == 0
    assert job.bus.last["daily_action_cap"] == 100
    assert job.bus.last["seconds_per_item"] == 8.6


async def test_unexpected_exception_marks_failed():
    conn = connect(":memory:")
    job = make(Raises, conn)
    await job.run()
    j = JobRepo(conn).get(job.job_id)
    assert (j.state, j.message) == ("failed", "boom")


async def test_logged_out_pauses_with_message_and_brings_browser_forward():
    conn = connect(":memory:")
    driver = FakeDriver()
    job = make(type("LO", (Raises,), {"exc": LoggedOut()}), conn, driver)
    await job.run()
    j = JobRepo(conn).get(job.job_id)
    assert (j.state, j.message) == ("paused", MSG_LOGIN)
    assert driver.front_calls == 1


class BringToFrontFailsDriver(FakeDriver):
    async def bring_to_front(self):
        raise RuntimeError("no window")


async def test_logged_out_pauses_even_if_bring_to_front_fails():
    conn = connect(":memory:")
    driver = BringToFrontFailsDriver()
    job = make(type("LO", (Raises,), {"exc": LoggedOut()}), conn, driver)
    await job.run()
    j = JobRepo(conn).get(job.job_id)
    assert (j.state, j.message) == ("paused", MSG_LOGIN)


async def test_rate_limited_pauses_with_message():
    conn = connect(":memory:")
    job = make(type("RL", (Raises,), {"exc": RateLimited()}), conn)
    await job.run()
    j = JobRepo(conn).get(job.job_id)
    assert (j.state, j.message) == ("paused", MSG_RATE_LIMITED)


async def test_request_pause_stops_at_next_checkpoint():
    conn = connect(":memory:")
    job = make(PausesItself, conn)
    job.request_pause()
    await job.run()
    j = JobRepo(conn).get(job.job_id)
    assert (j.state, j.done) == ("paused", 0)


async def test_runner_refuses_second_job_while_busy():
    conn = connect(":memory:")
    gate = asyncio.Event()

    class Waits(BaseJob):
        async def _run(self):
            await gate.wait()
            self.set_state("done")

    runner = JobRunner()
    runner.start(make(Waits, conn))
    assert runner.is_running()
    with pytest.raises(RuntimeError):
        runner.start(make(Finishes, conn))
    gate.set()
    await runner.wait()
    assert not runner.is_running()


async def test_notifier_called_once_per_transition():
    from igcleanup.jobs.engine import MSG_DONE_TODAY
    conn = connect(":memory:")
    toasts = []
    job = JobRepo(conn).create("unfollow", total=5)
    j = BaseJob(job.id, conn, FakeDriver(), Settings(), Pacer(Settings(), sleep=no_sleep),
                ProgressBus(), notifier=lambda t, m: toasts.append(m))
    j.set_state("running", done=1)
    j.set_state("running", done=2)
    j.set_state("paused", message=MSG_DONE_TODAY, done=3)
    j.set_state("paused", message=MSG_DONE_TODAY, done=3)  # same state again: no second toast
    j.set_state("done", done=5)
    assert toasts == ["Done for today, 2 to go.", "Unfollowing finished."]
