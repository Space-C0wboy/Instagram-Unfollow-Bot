from dataclasses import dataclass
from datetime import date
from typing import AsyncIterator, Protocol


@dataclass(frozen=True)
class Gone:
    pass


@dataclass(frozen=True)
class NeverPosted:
    profile_pic_url: str | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class Posted:
    last_post_date: date
    post_count: int
    profile_pic_url: str | None = None
    display_name: str | None = None


ProfileResult = Gone | NeverPosted | Posted


class DriverError(Exception):
    """Base for everything the driver can raise on purpose."""


class ParseError(DriverError):
    """Expected page elements were missing. Instagram may have changed layout."""


class RateLimited(DriverError):
    """Instagram showed a Try again later or We restrict certain activity dialog."""


class LoggedOut(DriverError):
    """Redirected to the login page."""


class PageLoadError(DriverError):
    """Navigation failed (network)."""


class ActionFailed(DriverError):
    """Follow/unfollow button did not change state after clicking."""


class Driver(Protocol):
    async def ensure_logged_in(self) -> None: ...
    async def is_logged_in(self) -> bool: ...
    def list_following(self) -> AsyncIterator[tuple[str, str]]: ...
    async def inspect_profile(self, username: str) -> ProfileResult: ...
    async def unfollow(self, username: str) -> None: ...
    async def follow(self, username: str) -> None: ...
    async def open_profile(self, username: str) -> None: ...
    async def open_url(self, url: str) -> None: ...
    async def fetch_image(self, url: str) -> bytes | None: ...
    async def own_username(self) -> str: ...
    async def bring_to_front(self) -> None: ...
    async def close(self) -> None: ...
