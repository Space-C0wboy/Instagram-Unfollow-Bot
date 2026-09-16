from igcleanup.jobs.pacing import Pacer
from igcleanup.settings import Settings


class Recorder:
    def __init__(self):
        self.slept: list[float] = []

    async def sleep(self, s: float):
        self.slept.append(s)


def midpoint(a, b):
    return (a + b) / 2


class NoJitter:
    """A random source that never jitters, never takes a break, and rests exactly on time."""
    def lognormvariate(self, mu, sigma): return 1.0
    def random(self): return 0.99
    def uniform(self, a, b): return (a + b) / 2


class AlwaysBreak(NoJitter):
    def random(self): return 0.0


async def test_after_profile_sleeps_delay_and_rests_every_n():
    rec = Recorder()
    p = Pacer(Settings(scan_delay_seconds=(3, 7), scan_rest_every=2, scan_rest_seconds=(120, 240)),
              sleep=rec.sleep, uniform=midpoint, rng=NoJitter())
    await p.after_profile(1)
    assert sum(rec.slept) == 5.0
    before = len(rec.slept)
    await p.after_profile(2)
    assert sum(rec.slept[before:]) == 5.0 + 180.0


async def test_before_action_uses_action_delay():
    rec = Recorder()
    p = Pacer(Settings(action_delay_seconds=(30, 90)), sleep=rec.sleep, uniform=midpoint, rng=NoJitter())
    await p.before_action()
    assert sum(rec.slept) == 60.0


async def test_before_action_stops_early_when_interrupted():
    rec = Recorder()
    calls = {"n": 0}

    def interrupt():
        calls["n"] += 1
        return calls["n"] > 1

    p = Pacer(Settings(action_delay_seconds=(30, 90)), sleep=rec.sleep, uniform=midpoint, rng=NoJitter())
    p.interrupt = interrupt
    await p.before_action()
    assert sum(rec.slept) <= 0.5


async def test_before_action_subtracts_elapsed_and_reports_wait():
    rec = Recorder()
    seen = []
    p = Pacer(Settings(action_delay_seconds=(30, 90)), sleep=rec.sleep, uniform=midpoint, rng=NoJitter())
    waited = await p.before_action(elapsed=50, on_wait=seen.append)
    assert waited == 10.0
    assert abs(sum(rec.slept) - 10.0) < 1e-9
    assert seen == [10.0]


async def test_before_action_skips_wait_when_gap_already_passed():
    rec = Recorder()
    seen = []
    p = Pacer(Settings(action_delay_seconds=(30, 90)), sleep=rec.sleep, uniform=midpoint, rng=NoJitter())
    assert await p.before_action(elapsed=600, on_wait=seen.append) == 0.0
    assert rec.slept == [] and seen == []


async def test_before_action_adds_a_break_sometimes():
    rec = Recorder()
    p = Pacer(Settings(action_delay_seconds=(30, 90)), sleep=rec.sleep, uniform=midpoint, rng=AlwaysBreak())
    waited = await p.before_action()
    assert waited == 60.0 + 150.0  # base delay plus a break of 2.5 typical gaps


async def test_human_delay_stays_within_bounds():
    import random
    p = Pacer(Settings(), rng=random.Random(7))
    for _ in range(500):
        d = p._human(30, 90)
        assert 21 <= d <= 225


async def test_rest_cadence_is_jittered_not_exact():
    import random
    p = Pacer(Settings(scan_rest_every=50), rng=random.Random(3))
    points = {p._jittered_rest_point(0) for _ in range(50)}
    assert all(35 <= x <= 65 for x in points)
    assert len(points) > 1
