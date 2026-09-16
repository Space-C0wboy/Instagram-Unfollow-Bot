import asyncio
import contextlib
import random
import re
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urljoin

from playwright.async_api import (BrowserContext, Page, Playwright, TimeoutError as PWTimeout,
                                  async_playwright, Error as PWError)

from igcleanup.driver import selectors as S
from igcleanup.driver.parsing import (detect_page_state, parse_following_count,
                                      parse_post_datetime, parse_profile)
from igcleanup.driver.types import (ActionFailed, DriverError, Gone, LoggedOut, NeverPosted,
                                    PageLoadError, ParseError, Posted, ProfileResult, RateLimited)

BASE = "https://www.instagram.com"
NAV_TIMEOUT_MS = 30_000
SCROLL_IDLE_ROUNDS = 5


class PlaywrightDriver:
    def __init__(self, profile_dir: Path, headless: bool = False,
                 viewport: tuple[int, int] = (1000, 850), position: tuple[int, int] = (20, 40)):
        self.profile_dir = profile_dir
        self.headless = headless
        self.viewport = viewport
        self.position = position
        self._pw: Playwright | None = None
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._ctx = await self._pw.chromium.launch_persistent_context(
            str(self.profile_dir), headless=self.headless,
            viewport={"width": self.viewport[0], "height": self.viewport[1]},
            args=[f"--window-position={self.position[0]},{self.position[1]}"],
            ignore_default_args=["--enable-automation"],
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()

    async def close(self) -> None:
        try:
            if self._ctx:
                await self._ctx.close()
        finally:
            if self._pw:
                await self._pw.stop()

    @property
    def page(self) -> Page:
        assert self._page is not None, "call start() first"
        return self._page

    @property
    def context(self) -> BrowserContext:
        assert self._ctx is not None, "call start() first"
        return self._ctx

    @property
    def playwright(self) -> Playwright:
        assert self._pw is not None, "call start() first"
        return self._pw

    async def _goto(self, url: str) -> str:
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            await self.page.wait_for_timeout(random.randint(1200, 2600))
            await self._dismiss_dialogs()
            html = await self.page.content()
        except (PWTimeout, PWError) as exc:
            raise PageLoadError(str(exc)) from exc
        state = detect_page_state(html, self.page.url)
        if state == "login":
            raise LoggedOut()
        if state == "rate_limited":
            raise RateLimited()
        return state

    async def fetch_image(self, url: str) -> bytes | None:
        """Download an image through the logged-in browser context; None on any failure."""
        try:
            resp = await self._ctx.request.get(url, timeout=10_000)
            if resp.ok and resp.headers.get("content-type", "").startswith("image/"):
                return await resp.body()
        except (PWTimeout, PWError):
            pass
        return None

    async def _act_human(self, clicks: int = 0) -> None:
        """A few unhurried mouse moves and small scrolls, like someone looking at the page."""
        with contextlib.suppress(PWTimeout, PWError):
            for _ in range(random.randint(1, 3)):
                await self.page.mouse.move(random.randint(200, 900), random.randint(150, 700),
                                           steps=random.randint(8, 25))
                await self.page.wait_for_timeout(random.randint(150, 600))
            if random.random() < 0.6:
                await self.page.mouse.wheel(0, random.randint(120, 500))
                await self.page.wait_for_timeout(random.randint(400, 1400))
                if random.random() < 0.5:
                    await self.page.mouse.wheel(0, -random.randint(80, 300))
                    await self.page.wait_for_timeout(random.randint(200, 700))

    async def _click_like_a_person(self, locator) -> None:
        with contextlib.suppress(PWTimeout, PWError):
            await locator.hover(timeout=5_000)
            await self.page.wait_for_timeout(random.randint(250, 900))
        await locator.click(timeout=10_000)

    async def is_logged_in(self) -> bool:
        if S.LOGIN_URL_MARKER in self.page.url:
            return False
        role, name = S.LOGGED_IN_MARKER_ROLE
        return await self.page.get_by_role(role, name=name).count() > 0

    async def ensure_logged_in(self) -> None:
        try:
            await self._goto(BASE + "/")
        except LoggedOut:
            pass
        while not await self.is_logged_in():
            await asyncio.sleep(3)
            await self._dismiss_dialogs()

    async def _dismiss_dialogs(self) -> None:
        """Close Instagram's optional prompts (e.g. 'Turn on notifications') if one is showing."""
        for text in S.DISMISS_DIALOG_TEXTS:
            btn = self.page.get_by_role("button", name=text, exact=True)
            if await btn.count():
                with contextlib.suppress(PWTimeout, PWError):
                    await btn.first.click(timeout=3_000)
                    await self.page.wait_for_timeout(500)
                return

    async def bring_to_front(self) -> None:
        await self.page.bring_to_front()

    async def open_url(self, url: str) -> None:
        """Show a page in the Instagram window (used for the terms of use). Best effort."""
        with contextlib.suppress(DriverError, PWTimeout, PWError):
            await self.page.goto(url, timeout=NAV_TIMEOUT_MS)
        await self.bring_to_front()

    async def open_profile(self, username: str) -> None:
        with contextlib.suppress(DriverError):
            await self._goto(f"{BASE}/{username}/")
        await self.bring_to_front()

    async def own_username(self) -> str:
        return await self._own_username()

    async def _own_username(self) -> str:
        await self._goto(BASE + "/")
        role, name = S.NAV_PROFILE_ROLE
        role_link = self.page.get_by_role(role, name=name, exact=True)
        if await role_link.count() > 0:
            href = (await role_link.first.get_attribute("href") or "").strip("/")
        else:
            link = self.page.locator(S.NAV_PROFILE_LINK)
            if await link.count() == 0:
                raise ParseError("Could not find own profile link in navigation")
            href = (await link.last.get_attribute("href") or "").strip("/")
        if not href or "/" in href:
            raise ParseError("Could not find own profile link in navigation")
        return href

    async def list_following(self) -> AsyncIterator[tuple[str, str]]:
        me = await self._own_username()
        await self._goto(f"{BASE}/{me}/")
        expected = parse_following_count(await self.page.content())
        following_link = self.page.get_by_role("link", name=re.compile(S.FOLLOWING_LINK_NAME_RE, re.I)).first
        try:
            await following_link.wait_for(timeout=15_000)
            await following_link.click(timeout=10_000)
        except (PWTimeout, PWError) as exc:
            raise ParseError("Could not open the Following list") from exc
        dialog = self.page.locator(S.FOLLOWING_DIALOG).last
        await dialog.wait_for(timeout=NAV_TIMEOUT_MS)
        collected: list[tuple[str, str]] = []
        seen: set[str] = set()
        idle = 0
        attempts = 0
        self.following_warning = None
        try:
          while True:  # one scroll pass; repeated a couple of times if the list came up short
            while idle < SCROLL_IDLE_ROUNDS:
                pairs = await dialog.evaluate(
                    "d => Array.from(d.querySelectorAll('" + S.DIALOG_USER_LINK_JS + "'))"
                    ".map(a => [a.getAttribute('href'), a.innerText])"
                )
                new = 0
                for href, text in pairs:
                    href = (href or "").strip("/")
                    if not href or "/" in href or href in seen:
                        continue
                    seen.add(href)
                    lines = (text or "").strip().splitlines()
                    display = lines[1] if len(lines) > 1 else ""
                    collected.append((href, display))
                    new += 1
                idle = 0 if new else idle + 1
                scrolled = await dialog.evaluate(
                    """d => {
                        const candidates = [...d.querySelectorAll('*'), d];
                        const el = candidates.find(e => e.scrollHeight > e.clientHeight);
                        if (!el) return false;
                        el.scrollTop = el.scrollHeight;
                        return true;
                    }"""
                )
                if not scrolled:
                    break
                await self.page.wait_for_timeout(1200)
            n = len(collected)
            if n >= 0.9 * expected or attempts >= 2:
                break
            attempts += 1  # Instagram stalled the list: rest a moment and scroll again
            await self.page.wait_for_timeout(random.randint(5000, 10000))
            idle = 0
        finally:
            await self.page.keyboard.press("Escape")

        n = len(collected)
        if n < 0.9 * expected:
            self.following_warning = f"Only {n} of {expected} accounts could be listed."

        for href, display in collected:
            yield href, display

    async def inspect_profile(self, username: str) -> ProfileResult:
        state = await self._goto(f"{BASE}/{username}/")
        if state == "gone":
            return Gone()
        header = parse_profile(await self.page.content())
        if header.post_count == 0:
            return NeverPosted(profile_pic_url=header.profile_pic_url,
                               display_name=header.display_name)
        if header.first_unpinned_post_href is not None:
            await self._goto(urljoin(BASE, header.first_unpinned_post_href))
            last = parse_post_datetime(await self.page.content())
        elif header.pinned_post_hrefs:
            # Every visible post is pinned (small accounts): the newest pinned one is the newest post.
            dates = []
            for href in header.pinned_post_hrefs[:3]:
                await self._goto(urljoin(BASE, href))
                dates.append(parse_post_datetime(await self.page.content()))
            last = max(dates)
        else:
            raise ParseError("Profile reports posts but no post tile found")
        return Posted(last_post_date=last, post_count=header.post_count,
                      profile_pic_url=header.profile_pic_url, display_name=header.display_name)

    async def _button_in(self, scope, texts: tuple[str, ...]):
        """First button inside `scope` whose visible text (first line) is one of `texts`.

        Instagram's buttons carry icons with their own labels, so their accessible
        names are not exact; the visible text is.
        """
        buttons = scope.get_by_role("button")
        for i in range(await buttons.count()):
            text = (await buttons.nth(i).inner_text()).strip().splitlines()
            if text and text[0].strip() in texts:
                return buttons.nth(i)
        return None

    async def _button_with_text(self, texts: tuple[str, ...]):
        return await self._button_in(self.page.locator(S.PROFILE_HEADER), texts)

    async def unfollow(self, username: str) -> None:
        state = await self._goto(f"{BASE}/{username}/")
        if state == "gone":
            raise ActionFailed("Profile is gone")
        try:
            await self.page.locator(S.PROFILE_HEADER).get_by_role("button").first.wait_for(timeout=10_000)
        except (PWTimeout, PWError) as exc:
            raise ParseError("Profile header did not render") from exc
        btn = await self._button_with_text(S.FOLLOWING_BUTTON_TEXTS)
        if btn is None:
            if await self._button_with_text(S.FOLLOW_BUTTON_TEXTS):
                return  # already not following
            raise ParseError("No follow state button found")
        await self._act_human()
        await self._click_like_a_person(btn)
        try:
            dialog = self.page.locator(S.DIALOG).last
            await dialog.wait_for(timeout=10_000)
            confirm = None
            for _ in range(20):  # menu items render shortly after the dialog container
                confirm = await self._button_in(dialog, (S.UNFOLLOW_CONFIRM_TEXT,))
                if confirm is not None:
                    break
                await self.page.wait_for_timeout(250)
            if confirm is None:
                raise ActionFailed("Unfollow confirmation did not appear")
            await self._click_like_a_person(confirm)
        except (PWTimeout, PWError) as exc:
            raise ActionFailed("Unfollow confirmation did not appear") from exc
        await self.page.wait_for_timeout(1500)
        if not await self._button_with_text(S.FOLLOW_BUTTON_TEXTS):
            raise ActionFailed("Button did not change to Follow")

    async def follow(self, username: str) -> None:
        state = await self._goto(f"{BASE}/{username}/")
        if state == "gone":
            raise ActionFailed("Profile is gone")
        try:
            await self.page.locator(S.PROFILE_HEADER).get_by_role("button").first.wait_for(timeout=10_000)
        except (PWTimeout, PWError) as exc:
            raise ParseError("Profile header did not render") from exc
        btn = await self._button_with_text(S.FOLLOW_BUTTON_TEXTS)
        if btn is None:
            if await self._button_with_text(S.FOLLOWING_BUTTON_TEXTS):
                return  # already following
            raise ParseError("No follow state button found")
        await self._act_human()
        await self._click_like_a_person(btn)
        await self.page.wait_for_timeout(1500)
        if not await self._button_with_text(S.FOLLOWING_BUTTON_TEXTS):
            raise ActionFailed("Button did not change to Following")
