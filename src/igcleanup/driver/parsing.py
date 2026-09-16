import re
from datetime import date, datetime
from typing import NamedTuple

from bs4 import BeautifulSoup

from igcleanup.driver import selectors as S
from igcleanup.driver.types import ParseError


class ProfileHeader(NamedTuple):
    post_count: int
    profile_pic_url: str | None
    first_unpinned_post_href: str | None
    display_name: str | None = None
    pinned_post_hrefs: tuple[str, ...] = ()


def parse_int_count(text: str) -> int:
    t = text.strip().replace(",", "")
    mult = 1
    if t.endswith("K"):
        mult, t = 1_000, t[:-1]
    elif t.endswith("M"):
        mult, t = 1_000_000, t[:-1]
    return int(round(float(t) * mult))


def detect_page_state(html: str, url: str) -> str:
    if S.LOGIN_URL_MARKER in url:
        return "login"
    soup = BeautifulSoup(html, "html.parser")
    for dialog in soup.select(S.DIALOG):
        text = dialog.get_text()
        if any(marker in text for marker in S.RATE_LIMIT_TEXTS):
            return "rate_limited"
    # A real profile page always carries og:description; the not-found page carries
    # no og: metas at all and shows the "Sorry" sentence in a plain element.
    if soup.select_one(S.META_DESCRIPTION) is None:
        title = soup.title.get_text() if soup.title else ""
        if S.GONE_TITLE in title:
            return "gone"
        for tag in S.GONE_TEXT_TAGS:
            for el in soup.find_all(tag):
                if el.get_text().strip() == S.GONE_TEXT:
                    return "gone"
    return "ok"


def parse_profile(html: str) -> ProfileHeader:
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.select_one(S.META_DESCRIPTION)
    content = meta.get("content", "") if meta else ""
    m = re.search(S.META_COUNTS_RE, content)
    if not m:
        raise ParseError("Profile counts not found in og:description")
    post_count = parse_int_count(m.group(3))

    # The logged-in user's own avatar sits in the sidebar before the profile's, so
    # prefer the avatar inside <header>; fall back to any matching alt text.
    pic = None
    scopes = [soup.find(S.PROFILE_HEADER), soup]
    for scope in scopes:
        if scope is None:
            continue
        for img in scope.find_all("img"):
            if str(img.get("alt", "")).endswith(S.PROFILE_PIC_ALT_SUFFIX):
                pic = img.get("src")
                break
        if pic:
            break

    first_href = None
    pinned: list[str] = []
    for a in soup.select(S.POST_TILE_LINK):
        if a.find(attrs={"aria-label": S.PINNED_ICON_ARIA}):
            if a.get("href"):
                pinned.append(a.get("href"))
            continue
        first_href = a.get("href")
        break

    display_name = None
    title_meta = soup.select_one(S.META_TITLE)
    if title_meta:
        title_content = title_meta.get("content", "")
        idx = title_content.find(" (@")
        if idx != -1:
            display_name = title_content[:idx]

    return ProfileHeader(post_count=post_count, profile_pic_url=pic,
                         first_unpinned_post_href=first_href, display_name=display_name,
                         pinned_post_hrefs=tuple(pinned))


def parse_following_count(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.select_one(S.META_DESCRIPTION)
    content = meta.get("content", "") if meta else ""
    m = re.search(S.META_COUNTS_RE, content)
    if not m:
        raise ParseError("Following count not found in og:description")
    return parse_int_count(m.group(2))


def parse_post_datetime(html: str) -> date:
    # A post page also lists comment timestamps; the post itself is always the earliest.
    soup = BeautifulSoup(html, "html.parser")
    stamps = []
    for t in soup.select(S.POST_TIME):
        raw = str(t.get("datetime", "")).replace("Z", "+00:00")
        try:
            stamps.append(datetime.fromisoformat(raw))
        except ValueError:
            continue
    if not stamps:
        raise ParseError("Post timestamp not found")
    return min(stamps).date()
