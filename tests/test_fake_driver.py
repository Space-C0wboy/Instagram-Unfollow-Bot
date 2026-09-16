from datetime import date

import pytest

from igcleanup.driver.types import ActionFailed, Gone, Posted, RateLimited
from tests.fake_driver import FakeDriver


async def test_list_following_and_inspect_scripted():
    d = FakeDriver(
        following=[("a", "A"), ("b", "B")],
        profiles={"a": Gone(), "b": [RateLimited(), Posted(date(2024, 1, 1), 3)]},
    )
    assert [u async for u in d.list_following()] == [("a", "A"), ("b", "B")]
    assert await d.inspect_profile("a") == Gone()
    with pytest.raises(RateLimited):
        await d.inspect_profile("b")
    assert await d.inspect_profile("b") == Posted(date(2024, 1, 1), 3)
    assert d.calls == [("list_following", ""), ("inspect", "a"), ("inspect", "b"), ("inspect", "b")]


async def test_unfollow_errors_consumed_then_succeed():
    d = FakeDriver(unfollow_errors={"x": [ActionFailed()]})
    with pytest.raises(ActionFailed):
        await d.unfollow("x")
    await d.unfollow("x")
    assert d.calls == [("unfollow", "x"), ("unfollow", "x")]


async def test_unknown_profile_is_loud():
    with pytest.raises(KeyError):
        await FakeDriver().inspect_profile("nobody")


async def test_fetch_image_is_scripted():
    d = FakeDriver(images={"http://x/p.jpg": b"jpegbytes"})
    assert await d.fetch_image("http://x/p.jpg") == b"jpegbytes"
    assert await d.fetch_image("http://x/missing.jpg") is None
