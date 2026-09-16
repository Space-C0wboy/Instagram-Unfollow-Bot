from typing import AsyncIterator

from igcleanup.driver.types import ProfileResult


class FakeDriver:
    def __init__(self, following=(), profiles=None, unfollow_errors=None,
                 follow_errors=None, logged_in=True, images=None, owner="me"):
        self.following = list(following)
        self.profiles = dict(profiles or {})
        self.unfollow_errors = {k: list(v) for k, v in (unfollow_errors or {}).items()}
        self.follow_errors = {k: list(v) for k, v in (follow_errors or {}).items()}
        self.logged_in = logged_in
        self.images = dict(images or {})
        self.owner = owner
        self.following_warning = None
        self.calls: list[tuple[str, str]] = []
        self.front_calls = 0

    async def ensure_logged_in(self) -> None:
        self.logged_in = True

    async def is_logged_in(self) -> bool:
        return self.logged_in

    async def list_following(self) -> AsyncIterator[tuple[str, str]]:
        self.calls.append(("list_following", ""))
        for pair in self.following:
            yield pair

    async def inspect_profile(self, username: str) -> ProfileResult:
        self.calls.append(("inspect", username))
        scripted = self.profiles[username]
        if isinstance(scripted, list):
            scripted = scripted.pop(0)
        if isinstance(scripted, Exception):
            raise scripted
        return scripted

    async def _action(self, kind: str, errors: dict, username: str) -> None:
        self.calls.append((kind, username))
        queue = errors.get(username)
        if queue:
            raise queue.pop(0)

    async def unfollow(self, username: str) -> None:
        await self._action("unfollow", self.unfollow_errors, username)

    async def follow(self, username: str) -> None:
        await self._action("follow", self.follow_errors, username)

    async def open_url(self, url: str) -> None:
        self.calls.append(("open_url", url))

    async def open_profile(self, username: str) -> None:
        self.calls.append(("open", username))

    async def own_username(self) -> str:
        return self.owner

    async def fetch_image(self, url: str) -> bytes | None:
        self.calls.append(("fetch_image", url))
        return self.images.get(url)

    async def bring_to_front(self) -> None:
        self.front_calls += 1

    async def close(self) -> None:
        pass
