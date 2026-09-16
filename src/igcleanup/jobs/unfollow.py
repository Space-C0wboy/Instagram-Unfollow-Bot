from datetime import date, datetime
from pathlib import Path
from typing import Callable

from igcleanup.driver.types import ActionFailed, PageLoadError, ParseError
from igcleanup.jobs.engine import MSG_CONNECTION, MSG_DONE_TODAY, BaseJob
from igcleanup.storage.accounts import AccountRepo
from igcleanup.storage.backup import write_backup
from igcleanup.storage.jobs import Job, JobRepo

MAX_CONSECUTIVE_LOAD_ERRORS = 3


def prepare_unfollow(conn, backups_dir: Path, now: Callable[[], datetime]) -> tuple[Job, Path]:
    targets = AccountRepo(conn).targets()
    if not targets:
        raise ValueError("Nothing selected")
    path = write_backup(backups_dir, targets, now())
    job = JobRepo(conn).create("unfollow", total=len(targets), started_at=now())
    return job, path


class UnfollowJob(BaseJob):
    def __init__(self, *args, today: Callable[[], date] = date.today, **kwargs):
        super().__init__(*args, **kwargs)
        self.today = today

    def _seconds_since_last_action(self) -> float | None:
        last = self.events.last_action_at()
        return None if last is None else max(0.0, (self.now() - last).total_seconds())

    def _cap_reached(self) -> bool:
        return self.events.count_on(self.today()) >= self.settings.daily_action_cap

    async def _run(self) -> None:
        await self.ensure_same_account()
        total = self.jobs.get(self.job_id).total
        remaining = self.accounts.targets()
        done = total - len(remaining)
        self.set_state("running", done=done)

        load_errors = 0
        for acct in remaining:
            if self.check_pause():
                return
            if not self.accounts.is_target(acct.username):
                continue  # unticked or kept while this run was going
            if self._cap_reached():
                self.set_state("paused", message=MSG_DONE_TODAY)
                return
            await self.pacer.before_action(
                self._seconds_since_last_action(),
                on_wait=lambda secs: self.set_state(
                    "running", done=done, message=f"Next unfollow in about {round(secs)} seconds"),
            )
            self.set_state("running", done=done, message=None)
            try:
                succeeded = await self._unfollow_with_retry(acct.username)
            except PageLoadError:
                load_errors += 1
                if load_errors >= MAX_CONSECUTIVE_LOAD_ERRORS:
                    self.set_state("paused", message=MSG_CONNECTION)
                    return
                continue
            load_errors = 0
            if succeeded:
                stamp = self.now()
                self.events.record(self.job_id, acct.username, "unfollow", stamp)
                self.accounts.mark_unfollowed(acct.username, stamp)
                self.log_action(f"unfollowed @{acct.username}")
            else:
                self.accounts.set_status(acct.username, "error")
                self.log_action(f"could not unfollow @{acct.username}")
                self.accounts.set_selected([acct.username], False)
            done += 1
            self.set_state("running", done=done, current=acct.username)

        if self.accounts.targets():
            self.set_state("paused", message=MSG_CONNECTION, done=done)
        else:
            self.set_state("done", done=done)

    async def _unfollow_with_retry(self, username: str) -> bool:
        try:
            await self.driver.unfollow(username)
            return True
        except (ActionFailed, ParseError):
            pass
        await self.pacer.before_action()
        try:
            await self.driver.unfollow(username)
            return True
        except (ActionFailed, ParseError):
            return False
