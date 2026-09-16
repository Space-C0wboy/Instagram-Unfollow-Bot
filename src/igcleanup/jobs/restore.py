from datetime import date, datetime
from pathlib import Path
from typing import Callable

from igcleanup.driver.types import ActionFailed, PageLoadError, ParseError
from igcleanup.jobs.engine import MSG_CONNECTION, MSG_DONE_TODAY, BaseJob
from igcleanup.storage.backup import read_backup, write_rows
from igcleanup.storage.jobs import Job, JobRepo

MAX_CONSECUTIVE_LOAD_ERRORS = 3


def prepare_restore(conn, backup_path: Path, now: Callable[[], datetime],
                    usernames: list[str] | None = None) -> Job:
    """Create a restore job for every row of the backup, or only `usernames` if given.

    A selection is written to its own file beside the backup so a resumed job still
    knows exactly which accounts were chosen.
    """
    rows = read_backup(backup_path)
    source = backup_path
    if usernames is not None:
        wanted = set(usernames)
        rows = [row for row in rows if row.username in wanted]
        if not rows:
            raise ValueError("Nothing selected")
        source = backup_path.parent / f"restore-selection-{now():%Y-%m-%d-%H%M%S}.csv"
        write_rows(source, rows)
    return JobRepo(conn).create("restore", total=len(rows), started_at=now(),
                                source=str(source))


class RestoreJob(BaseJob):
    def __init__(self, *args, backup_path: Path, today: Callable[[], date] = date.today,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.backup_path = backup_path
        self.today = today
        self.failed: list[str] = []

    def _seconds_since_last_action(self) -> float | None:
        last = self.events.last_action_at()
        return None if last is None else max(0.0, (self.now() - last).total_seconds())

    def _cap_reached(self) -> bool:
        return self.events.count_on(self.today()) >= self.settings.daily_action_cap

    async def _run(self) -> None:
        await self.ensure_same_account()
        rows = read_backup(self.backup_path)
        already = self.events.usernames_for_job(self.job_id)
        remaining = [r for r in rows if r.username not in already]
        done = len(rows) - len(remaining)
        self.set_state("running", done=done)

        load_errors = 0
        for row in remaining:
            if self.check_pause():
                return
            if self._cap_reached():
                self.set_state("paused", message=MSG_DONE_TODAY)
                return
            await self.pacer.before_action(
                self._seconds_since_last_action(),
                on_wait=lambda secs: self.set_state(
                    "running", done=done, message=f"Next follow in about {round(secs)} seconds"),
            )
            self.set_state("running", done=done, message=None)
            try:
                succeeded = await self._follow_with_retry(row.username)
            except PageLoadError:
                load_errors += 1
                if load_errors >= MAX_CONSECUTIVE_LOAD_ERRORS:
                    self.set_state("paused", message=MSG_CONNECTION)
                    return
                continue
            load_errors = 0
            if succeeded:
                self.events.record(self.job_id, row.username, "follow", self.now())
                self.log_action(f"followed @{row.username} again")
            else:
                self.failed.append(row.username)
                self.log_action(f"could not follow @{row.username} again")
            done += 1
            self.set_state("running", done=done, current=row.username)

        recorded = self.events.usernames_for_job(self.job_id)
        skipped = [r.username for r in rows if r.username not in recorded
                   and r.username not in self.failed]
        if skipped:
            self.set_state("paused", message=MSG_CONNECTION, done=done)
            return

        restored = len(recorded)
        message = f"Restored {restored} accounts."
        if self.failed:
            message += " Could not re-follow: " + ", ".join(self.failed)
        self.set_state("done", done=done, message=message)

    async def _follow_with_retry(self, username: str) -> bool:
        try:
            await self.driver.follow(username)
            return True
        except (ActionFailed, ParseError):
            pass
        await self.pacer.before_action()
        try:
            await self.driver.follow(username)
            return True
        except (ActionFailed, ParseError):
            return False
