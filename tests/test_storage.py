from datetime import date, datetime

import pytest

from igcleanup.storage.accounts import AccountRepo
from igcleanup.storage.db import connect
from igcleanup.storage.jobs import EventRepo, JobRepo


@pytest.fixture
def conn():
    return connect(":memory:")


def test_upsert_following_inserts_pending_and_keeps_existing_status(conn):
    repo = AccountRepo(conn)
    repo.upsert_following([("alice", "Alice"), ("bob", "Bob")])
    assert repo.pending_usernames() == ["alice", "bob"]
    repo.set_result("alice", status="gone", checked_at=datetime(2026, 1, 1))
    repo.upsert_following([("alice", "Alice R"), ("carol", "Carol")])
    assert repo.get("alice").status == "gone"
    assert repo.get("alice").display_name == "Alice R"
    assert repo.pending_usernames() == ["bob", "carol"]
    assert repo.get("alice").profile_url == "https://www.instagram.com/alice/"


def test_set_result_and_set_pending(conn):
    repo = AccountRepo(conn)
    repo.upsert_following([("alice", "Alice")])
    repo.set_result(
        "alice", status="inactive", post_count=10,
        last_post_date=date(2024, 1, 1), profile_pic_url="http://x/p.jpg",
        checked_at=datetime(2026, 1, 1),
    )
    a = repo.get("alice")
    assert (a.status, a.post_count, a.last_post_date) == ("inactive", 10, date(2024, 1, 1))
    repo.set_pending("alice")
    assert repo.get("alice").status == "pending"


def test_set_result_display_name_coalesce(conn):
    repo = AccountRepo(conn)
    repo.upsert_following([("alice", "Alice")])
    repo.set_result("alice", status="active", display_name="New", checked_at=datetime(2026, 1, 1))
    assert repo.get("alice").display_name == "New"
    repo.set_result("alice", status="active", display_name=None, checked_at=datetime(2026, 1, 1))
    assert repo.get("alice").display_name == "New"


def test_default_selection_and_targets(conn):
    repo = AccountRepo(conn)
    repo.upsert_following([(u, u) for u in ["a", "b", "c", "d"]])
    ts = datetime(2026, 1, 1)
    repo.set_result("a", status="inactive", checked_at=ts)
    repo.set_result("b", status="never_posted", checked_at=ts)
    repo.set_result("c", status="gone", checked_at=ts)
    repo.set_result("d", status="error", checked_at=ts)
    repo.apply_default_selection()
    assert {x.username for x in repo.targets()} == {"a", "c"}
    repo.set_selected(["b"], True)
    repo.set_selected(["a"], False)
    assert {x.username for x in repo.targets()} == {"b", "c"}
    repo.mark_unfollowed("c", datetime(2026, 1, 2))
    assert {x.username for x in repo.targets()} == {"b"}


def test_rebucket_only_touches_active_and_inactive(conn):
    repo = AccountRepo(conn)
    repo.upsert_following([(u, u) for u in ["old", "new", "gone"]])
    ts = datetime(2026, 1, 1)
    repo.set_result("old", status="active", last_post_date=date(2024, 1, 1), checked_at=ts)
    repo.set_result("new", status="inactive", last_post_date=date(2026, 6, 1), checked_at=ts)
    repo.set_result("gone", status="gone", checked_at=ts)
    repo.rebucket(threshold=date(2025, 9, 14))
    assert repo.get("old").status == "inactive"
    assert repo.get("new").status == "active"
    assert repo.get("gone").status == "gone"


def test_reset_for_rescan(conn):
    repo = AccountRepo(conn)
    repo.upsert_following([("a", "a")])
    repo.reset_for_rescan()
    assert repo.count() == 0


def test_jobs_lifecycle(conn):
    jobs = JobRepo(conn)
    j = jobs.create("scan", total=10)
    assert (j.state, j.done, j.total) == ("running", 0, 10)
    assert jobs.active().id == j.id
    jobs.update(j.id, done=3, state="paused", message="Waiting")
    assert jobs.get(j.id).message == "Waiting"
    assert jobs.active().id == j.id
    jobs.update(j.id, state="done", message=None, finished_at=datetime(2026, 1, 1))
    assert jobs.active() is None
    assert jobs.latest("scan").id == j.id
    assert jobs.latest("unfollow") is None
    r = jobs.create("restore", total=2, source="backups/x.csv")
    assert jobs.get(r.id).source == "backups/x.csv"
    assert j.source is None


def test_events_daily_count(conn):
    jobs = JobRepo(conn)
    j = jobs.create("unfollow", total=3)
    ev = EventRepo(conn)
    ev.record(j.id, "a", "unfollow", datetime(2026, 9, 14, 23, 59))
    ev.record(j.id, "b", "follow", datetime(2026, 9, 14, 8, 0))
    ev.record(j.id, "c", "unfollow", datetime(2026, 9, 15, 0, 1))
    assert ev.count_on(date(2026, 9, 14)) == 2
    assert ev.count_on(date(2026, 9, 15)) == 1
    assert ev.usernames_for_job(j.id) == {"a", "b", "c"}


def test_pause_stale_running_pauses_running_and_leaves_done_alone(conn):
    jobs = JobRepo(conn)
    running = jobs.create("scan", total=5)
    done = jobs.create("unfollow", total=1)
    jobs.update(done.id, state="done", message="all good", finished_at=datetime(2026, 1, 1))
    jobs.pause_stale_running()
    r = jobs.get(running.id)
    assert (r.state, r.message) == ("paused", None)
    d = jobs.get(done.id)
    assert (d.state, d.message) == ("done", "all good")


def test_set_status_changes_only_status(conn):
    repo = AccountRepo(conn)
    repo.upsert_following([("a", "A")])
    repo.set_result("a", status="inactive", post_count=7, checked_at=datetime(2026, 1, 1))
    repo.set_status("a", "error")
    a = repo.get("a")
    assert (a.status, a.post_count) == ("error", 7)


def test_keep_list_excludes_from_targets_and_survives_rescan(conn):
    repo = AccountRepo(conn)
    repo.upsert_following([("a", "A"), ("b", "B")])
    ts = datetime(2026, 1, 1)
    repo.set_result("a", status="inactive", checked_at=ts)
    repo.set_result("b", status="inactive", checked_at=ts)
    repo.apply_default_selection()
    repo.keep("a", ts)
    assert repo.kept_usernames() == {"a"}
    assert [x.username for x in repo.targets()] == ["b"]
    repo.apply_default_selection()  # a stays unselected even when defaults are reapplied
    assert [x.username for x in repo.targets()] == ["b"]
    assert repo.status_counts() == {"kept": 1, "inactive": 1}
    repo.reset_for_rescan()
    assert repo.kept_usernames() == {"a"}
    repo.unkeep("a")
    assert repo.kept_usernames() == set()


def test_rebucket_clears_ticks_that_become_active(conn):
    repo = AccountRepo(conn)
    repo.upsert_following([("a", "A"), ("b", "B")])
    ts = datetime(2026, 1, 1)
    repo.set_result("a", status="inactive", last_post_date=date(2025, 6, 1), checked_at=ts)
    repo.set_result("b", status="inactive", last_post_date=date(2024, 6, 1), checked_at=ts)
    repo.apply_default_selection()
    assert {x.username for x in repo.targets()} == {"a", "b"}
    repo.rebucket(threshold=date(2025, 1, 1))  # a is now active
    assert repo.get("a").status == "active" and repo.get("a").selected is False
    assert [x.username for x in repo.targets()] == ["b"]
    assert repo.is_target("b") and not repo.is_target("a")


def test_meta_repo(conn):
    from igcleanup.storage.meta import MetaRepo
    m = MetaRepo(conn)
    assert m.get("owner") is None
    m.set("owner", "first"); m.set("owner", "second")
    assert m.get("owner") == "second"
    m.delete("owner")
    assert m.get("owner") is None


def test_stale_pending_remove_not_in_and_selection_since(conn):
    repo = AccountRepo(conn)
    repo.upsert_following([("fresh", "F"), ("old", "O"), ("err", "E"), ("left", "L")])
    repo.set_result("fresh", status="inactive", checked_at=datetime(2026, 9, 10))
    repo.set_result("old", status="inactive", checked_at=datetime(2026, 7, 1))
    repo.set_result("err", status="error", checked_at=datetime(2026, 9, 12))
    repo.set_result("left", status="inactive", checked_at=datetime(2026, 9, 12))
    repo.apply_default_selection()
    repo.set_selected(["fresh"], False)  # the user's choice on a recent row
    assert repo.remove_not_in(["fresh", "old", "err", "new"]) == 1
    assert repo.get("left") is None
    assert repo.mark_stale_pending(datetime(2026, 8, 15)) == 2  # old and err
    assert repo.unchecked_count() == 2
    assert repo.get("fresh").status == "inactive"
    repo.set_result("old", status="inactive", checked_at=datetime(2026, 9, 15))
    repo.apply_default_selection(since=datetime(2026, 9, 14))
    assert repo.get("old").selected is True
    assert repo.get("fresh").selected is False  # untouched by the scoped default
    repo.keep("old", datetime(2026, 9, 15))
    repo.reset_all()
    assert repo.count() == 0 and repo.kept_usernames() == set()
