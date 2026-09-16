from datetime import date
from pathlib import Path

import pytest

from igcleanup.driver.parsing import (detect_page_state, parse_following_count, parse_int_count,
                                      parse_post_datetime, parse_profile)
from igcleanup.driver.types import ParseError

FIX = Path(__file__).parent / "fixtures" / "html"


def load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


PROFILE_URL = "https://www.instagram.com/alice/"


def test_parse_int_count():
    assert parse_int_count("1,234") == 1234
    assert parse_int_count("12.5K") == 12500
    assert parse_int_count("1.2M") == 1200000
    assert parse_int_count("0") == 0


def test_detect_page_state_orders():
    assert detect_page_state(load("login_page.html"), "https://www.instagram.com/accounts/login/") == "login"
    assert detect_page_state(load("rate_limited.html"), PROFILE_URL) == "rate_limited"
    assert detect_page_state(load("profile_gone.html"), PROFILE_URL) == "gone"
    assert detect_page_state(load("profile_active.html"), PROFILE_URL) == "ok"


def test_detect_page_state_ignores_marker_text_in_page_body():
    html = """<html><head>
<title>Alice (@alice)</title>
<meta property="og:description" content="1,234 Followers, 567 Following, 89 Posts - See Instagram photos and videos from Alice (@alice)">
</head><body>
<p>Try again later, we restrict certain activity</p>
<span>Sorry, this page isn't available.</span>
</body></html>"""
    assert detect_page_state(html, PROFILE_URL) == "ok"


def test_parse_profile_active_picks_first_tile():
    h = parse_profile(load("profile_active.html"))
    assert h.post_count == 89
    assert h.profile_pic_url == "https://cdn.example/alice.jpg"
    assert h.first_unpinned_post_href == "/alice/p/AAA111/"
    assert h.display_name == "Alice"


def test_parse_following_count():
    assert parse_following_count(load("profile_active.html")) == 567


def test_parse_profile_skips_pinned_tiles():
    h = parse_profile(load("profile_pinned_first.html"))
    assert h.post_count == 3
    assert h.first_unpinned_post_href == "/bob/p/NEWEST3/"


def test_parse_profile_never_posted():
    h = parse_profile(load("profile_never_posted.html"))
    assert h.post_count == 0
    assert h.first_unpinned_post_href is None


def test_parse_profile_without_counts_raises():
    with pytest.raises(ParseError):
        parse_profile("<html><body>nothing here</body></html>")


def test_parse_post_datetime():
    assert parse_post_datetime(load("post.html")) == date(2025, 3, 9)
    with pytest.raises(ParseError):
        parse_post_datetime("<html><body>no time</body></html>")


def test_parse_profile_prefers_header_avatar_over_sidebar():
    h = parse_profile(load("profile_active.html"))
    assert h.profile_pic_url == "https://cdn.example/alice.jpg"


def test_parse_post_datetime_uses_earliest_timestamp():
    html = ('<html><body><time datetime="2026-09-15T22:55:37.000Z"></time>'
            '<time datetime="2025-03-09T14:22:05.000Z"></time></body></html>')
    assert parse_post_datetime(html) == date(2025, 3, 9)


def test_real_profile_active():
    h = parse_profile(load("real_profile_active.html"))
    assert h.post_count == 172
    assert h.display_name == "Flipper Zero"
    assert h.profile_pic_url and "cdninstagram.com" in h.profile_pic_url
    assert h.first_unpinned_post_href and h.first_unpinned_post_href.startswith("/flipper_zero/")
    assert detect_page_state(load("real_profile_active.html"), "https://www.instagram.com/flipper_zero/") == "ok"


def test_real_profile_pinned_skips_pinned_tiles():
    h = parse_profile(load("real_profile_pinned.html"))
    assert h.post_count == 1983
    assert h.first_unpinned_post_href == "/hackthebox/p/DdUW6REGFnZ/"


def test_real_profile_gone():
    html = load("real_profile_gone.html")
    assert detect_page_state(html, "https://www.instagram.com/thisaccountdoesnotexist_9f8e7d/") == "gone"
    with pytest.raises(ParseError):
        parse_profile(html)


def test_real_post_datetime():
    assert parse_post_datetime(load("real_post.html")) == date(2026, 9, 15)


def test_parse_profile_reports_pinned_hrefs():
    h = parse_profile(load("profile_pinned_first.html"))
    assert h.pinned_post_hrefs == ("/bob/p/PINNED1/", "/bob/p/PINNED2/")
    assert parse_profile(load("profile_active.html")).pinned_post_hrefs == ()
