import contextlib
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from igcleanup.driver.types import Gone, NeverPosted, PageLoadError, ParseError, Posted
from igcleanup.jobs.engine import MSG_CONNECTION, MSG_LAYOUT, BaseJob, months_ago

MAX_CONSECUTIVE_PARSE_ERRORS = 5
MAX_CONSECUTIVE_LOAD_ERRORS = 3
UPSERT_BATCH = 100


class ScanJob(BaseJob):
    def __init__(
        self,
        *args,
        today: Callable[[], date] = date.today,
        avatars_dir: Path | None = None,
        refresh: bool = False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.today = today
        self.avatars_dir = avatars_dir
        self.refresh = refresh  # "Scan again": keep results, re-check only what is stale

    async def _cache_avatar(self, username: str, url: str | None) -> str | None:
        """Save the profile picture beside the database; CDN links expire, a file does not."""
        if not url or self.avatars_dir is None:
            return url
        data = await self.driver.fetch_image(url)
        if not data:
            return url
        self.avatars_dir.mkdir(parents=True, exist_ok=True)
        (self.avatars_dir / f"{username}.jpg").write_bytes(data)
        return f"/avatars/{username}.jpg"

    async def _collect_following(self) -> bool:
        """Read the Following list into the database; True if the user paused partway."""
        # Rows are saved as they arrive, so a listing that dies partway must be redone on resume.
        self.meta.set("listing_incomplete", str(self.job_id))
        listed: list[str] = []
        batch: list[tuple[str, str]] = []
        # aclosing: on pause the driver's cleanup (closing the dialog) runs now, not at garbage collection.
        async with contextlib.aclosing(self.driver.list_following()) as following:
            async for pair in following:
                if self.check_pause():
                    return True  # listing_incomplete stays set, so resume reads the list again
                listed.append(pair[0])
                batch.append(pair)
                if len(batch) >= UPSERT_BATCH:
                    self.accounts.upsert_following(batch)
                    batch = []
                    self.set_state(
                        "running",
                        total=self.accounts.count(),
                        message=f"Reading your Following list: {len(listed):,} found so far",
                    )
        if batch:
            self.accounts.upsert_following(batch)
        self.meta.delete("listing_incomplete")
        if self.refresh:
            self.accounts.remove_not_in(listed)
            cutoff = self.now() - timedelta(days=max(1, self.settings.rescan_after_days))
            self.accounts.mark_stale_pending(cutoff)
            self.meta.set("refresh_listed", str(self.job_id))
        self.set_state("running", total=self.accounts.count(), message=None)
        return False

    async def _run(self) -> None:
        await self.ensure_same_account()
        started = self.now()
        already_listed = self.refresh and self.meta.get("refresh_listed") == str(self.job_id)
        if (
            self.accounts.count() == 0
            or (self.refresh and not already_listed)
            or self.meta.get("listing_incomplete")
        ):
            if await self._collect_following():
                return
            warning = getattr(self.driver, "following_warning", None)
            if warning:
                self.meta.set("scan_warning", warning)
            else:
                self.meta.delete("scan_warning")

        threshold = months_ago(self.today(), self.settings.inactivity_months)
        pending = self.accounts.pending_usernames()
        total = self.accounts.count()
        done = total - len(pending)
        self.set_state("running", done=done, total=total)

        parse_errors = load_errors = visited = 0
        for username in pending:
            if self.check_pause():
                return
            try:
                result = await self.driver.inspect_profile(username)
            except PageLoadError:
                load_errors += 1
                if load_errors >= MAX_CONSECUTIVE_LOAD_ERRORS:
                    self.set_state("paused", message=MSG_CONNECTION)
                    return
                continue
            except ParseError:
                load_errors = 0
                parse_errors += 1
                self.accounts.set_result(username, status="error", checked_at=self.now())
                self.log_action(f"checked @{username}: could not read the profile")
                if parse_errors >= MAX_CONSECUTIVE_PARSE_ERRORS:
                    self.set_state("paused", message=MSG_LAYOUT, done=done + 1)
                    return
            else:
                load_errors = parse_errors = 0
                pic = await self._cache_avatar(username, getattr(result, "profile_pic_url", None))
                self._record(username, result, threshold, pic)
                self.log_action(
                    f"checked @{username}: {self.accounts.get(username).status}"
                    + (
                        f" (last post {result.last_post_date})"
                        if isinstance(result, Posted)
                        else ""
                    )
                )

            done += 1
            visited += 1
            self.set_state("running", done=done, current=username)
            await self.pacer.after_profile(visited)

        if self.accounts.pending_usernames():
            self.set_state(
                "paused",
                message=MSG_CONNECTION,
                done=self.accounts.count() - len(self.accounts.pending_usernames()),
            )
            return

        # Only rows checked in this run get their default tick; the user's earlier choices stand.
        self.accounts.apply_default_selection(since=started)
        self.set_state(
            "done",
            done=self.accounts.count() - len(self.accounts.pending_usernames()),
            message=self.meta.get("scan_warning"),
        )

    def _record(self, username: str, result, threshold: date, pic: str | None = None) -> None:
        now = self.now()
        if isinstance(result, Gone):
            self.accounts.set_result(username, status="gone", checked_at=now)
        elif isinstance(result, NeverPosted):
            self.accounts.set_result(
                username,
                status="never_posted",
                post_count=0,
                profile_pic_url=pic,
                display_name=result.display_name,
                checked_at=now,
            )
        elif isinstance(result, Posted):
            status = "inactive" if result.last_post_date < threshold else "active"
            self.accounts.set_result(
                username,
                status=status,
                post_count=result.post_count,
                last_post_date=result.last_post_date,
                profile_pic_url=pic,
                display_name=result.display_name,
                checked_at=now,
            )
        else:
            raise TypeError(f"Unexpected profile result: {result!r}")
