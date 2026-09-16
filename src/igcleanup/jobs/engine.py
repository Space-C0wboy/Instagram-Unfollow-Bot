import asyncio
import contextlib
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from igcleanup.driver.types import Driver, LoggedOut, RateLimited
from igcleanup.jobs.pacing import Pacer
from igcleanup.jobs.progress import ProgressBus
from igcleanup.settings import Settings
from igcleanup.storage.accounts import AccountRepo
from igcleanup.storage.jobs import EventRepo, JobRepo, _UNSET
from igcleanup.storage.meta import MetaRepo

MSG_LOGIN = "Please log in again."
MSG_CONNECTION = "Check your connection, then press Resume."
MSG_RATE_LIMITED = "Instagram asked us to slow down. Wait a few hours, then press Resume."
MSG_LAYOUT = ("Instagram may have changed its layout, or is temporarily limiting this account. "
              "Wait an hour and press Resume; if it keeps happening, this tool needs an update.")
MSG_DONE_TODAY = "Done for today, come back tomorrow."


JOB_LABELS = {"scan": "Scan", "unfollow": "Unfollowing", "restore": "Restoring"}
NOTIFY_ON_PAUSE = (MSG_DONE_TODAY, MSG_LOGIN, MSG_RATE_LIMITED, MSG_CONNECTION, MSG_LAYOUT)


def notification_text(job) -> str | None:
    """What a desktop toast should say for this job state, or None for no toast."""
    label = JOB_LABELS.get(job.type, job.type)
    if job.state == "done":
        return f"{label} finished."
    if job.state == "failed":
        return f"{label} stopped: {job.message}"
    if job.state == "paused" and job.message in NOTIFY_ON_PAUSE:
        if job.message == MSG_DONE_TODAY:
            left = max(0, job.total - job.done)
            return f"Done for today, {left} to go." if left else "Done for today."
        return job.message
    return None


class WrongAccount(Exception):
    """A different Instagram account is logged in than the one this installation was set up for."""


def months_ago(d: date, months: int) -> date:
    year, month = d.year, d.month - months
    while month <= 0:
        year -= 1
        month += 12
    return date(year, month, min(d.day, 28))


def seconds_per_item(settings: Settings, job_type: str) -> float:
    def mean(pair: tuple[float, float]) -> float:
        return (pair[0] + pair[1]) / 2

    if job_type == "scan":
        return (mean(settings.scan_delay_seconds)
                + mean(settings.scan_rest_seconds) / max(1, settings.scan_rest_every))
    return mean(settings.action_delay_seconds)


class BaseJob:
    def __init__(self, job_id: int, conn, driver: Driver, settings,
                 pacer: Pacer, bus: ProgressBus, now: Callable[[], datetime] = datetime.now,
                 notifier: Callable[[str, str], None] | None = None,
                 activity_log: Path | None = None):
        self.notifier = notifier
        self.activity_log = activity_log
        self.job_id = job_id
        self.conn = conn
        self.driver = driver
        self._settings = settings  # a Settings, or a callable returning the current one
        self.pacer = pacer
        self.bus = bus
        self.now = now
        self.jobs = JobRepo(conn)
        self.accounts = AccountRepo(conn)
        self.events = EventRepo(conn)
        self.meta = MetaRepo(conn)
        self.stop_requested = False
        self.pacer.interrupt = lambda: self.stop_requested

    def log_action(self, text: str) -> None:
        """One timestamped line per thing the tool did, in a plain file the user can open."""
        if self.activity_log is None:
            return
        try:
            with self.activity_log.open("a", encoding="utf-8") as f:
                f.write(f"{self.now():%Y-%m-%d %H:%M:%S}  {text}\n")
        except OSError:
            pass  # a full or locked disk must never stop a job

    @property
    def settings(self) -> Settings:
        """Always the live settings, so a change saved mid-run applies at the next check."""
        return self._settings() if callable(self._settings) else self._settings

    async def ensure_same_account(self) -> None:
        """Refuse to act on a different Instagram account than this installation was set up for."""
        me = await self.driver.own_username()
        owner = self.meta.get("owner")
        if owner is None:
            self.meta.set("owner", me)
        elif owner != me:
            raise WrongAccount(
                f"A different Instagram account is logged in (@{me}). This tool was set up for "
                f"@{owner}. Log in as @{owner}, or press Scan again to start over with @{me}.")

    def request_pause(self) -> None:
        self.stop_requested = True

    def set_state(self, state: str, *, message=_UNSET, done: int | None = None,
                  total: int | None = None, current: str | None = None) -> None:
        finished_at = self.now() if state in ("done", "failed") else None
        before = self.jobs.get(self.job_id)
        self.jobs.update(self.job_id, state=state, done=done, total=total,
                         message=message, finished_at=finished_at)
        job = self.jobs.get(self.job_id)
        if (job.state, job.message) != (before.state, before.message):
            label = JOB_LABELS.get(job.type, job.type)
            if job.state != "running":
                self.log_action(f"{label} {job.state}" + (f": {job.message}" if job.message else ""))
            if self.notifier is not None:
                text = notification_text(job)
                if text:
                    self.notifier("Instagram Cleanup", text)
        self.bus.publish({
            "job_id": job.id, "type": job.type, "state": job.state, "done": job.done,
            "total": job.total, "message": job.message, "current": current,
            "actions_today": self.events.count_on(self.now().date()),
            "daily_action_cap": self.settings.daily_action_cap,
            "seconds_per_item": seconds_per_item(self.settings, job.type),
        })

    def check_pause(self) -> bool:
        if self.stop_requested:
            self.set_state("paused", message=None)
            return True
        return False

    async def run(self) -> None:
        job = self.jobs.get(self.job_id)
        self.log_action(f"{JOB_LABELS.get(job.type, job.type)} started ({job.done} of {job.total} done so far)")
        self.set_state("running", message=None)
        try:
            await self._run()
        except LoggedOut:
            self.set_state("paused", message=MSG_LOGIN)
            with contextlib.suppress(Exception):
                await self.driver.bring_to_front()
        except RateLimited:
            self.set_state("paused", message=MSG_RATE_LIMITED)
        except Exception as exc:  # noqa: BLE001 - the UI must show something
            self.set_state("failed", message=str(exc) or exc.__class__.__name__)

    async def _run(self) -> None:
        raise NotImplementedError


class JobRunner:
    def __init__(self):
        self.current: BaseJob | None = None
        self._task: asyncio.Task | None = None

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, job: BaseJob) -> None:
        if self.is_running():
            raise RuntimeError("A job is already running")
        self.current = job
        self._task = asyncio.create_task(job.run())

    def request_pause(self) -> None:
        if self.current and self.is_running():
            self.current.request_pause()

    async def wait(self) -> None:
        if self._task:
            await self._task
