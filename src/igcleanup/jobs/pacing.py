import asyncio
import random
from typing import Callable

from igcleanup.settings import Settings

SLEEP_SLICE_SECONDS = 0.5


class Pacer:
    def __init__(self, settings, sleep=asyncio.sleep, uniform=random.uniform,
                 rng: random.Random | None = None):
        self._settings = settings  # a Settings, or a callable returning the current one
        self._sleep = sleep
        self._uniform = uniform
        self._rng = rng or random.Random()
        self.interrupt: Callable[[], bool] = lambda: False
        self._next_rest = self._jittered_rest_point(0)

    @property
    def settings(self) -> Settings:
        return self._settings() if callable(self._settings) else self._settings

    # People do not act on a metronome: the base delay gets a long-tailed multiplier,
    # there are occasional longer breaks, and rests come at irregular intervals.
    def _human(self, lo: float, hi: float) -> float:
        base = self._uniform(lo, hi)
        jitter = self._rng.lognormvariate(0.0, 0.35)
        return min(max(base * jitter, lo * 0.7), hi * 2.5)

    def _jittered_rest_point(self, after: int) -> int:
        every = max(1, self.settings.scan_rest_every)
        return after + max(1, round(every * self._rng.uniform(0.7, 1.3)))

    async def _sleep_until_interrupted(self, seconds: float) -> None:
        remaining = seconds
        while remaining > 0:
            if self.interrupt():
                return
            slice_ = min(SLEEP_SLICE_SECONDS, remaining)
            await self._sleep(slice_)
            remaining -= slice_

    async def after_profile(self, index: int) -> None:
        delay = self._human(*self.settings.scan_delay_seconds)
        if self._rng.random() < 0.05:  # got distracted for a moment (2 to 8 typical delays)
            delay += self._rng.uniform(2, 8) * sum(self.settings.scan_delay_seconds) / 2
        await self._sleep_until_interrupted(delay)
        if index >= self._next_rest:
            self._next_rest = self._jittered_rest_point(index)
            await self._sleep_until_interrupted(self._human(*self.settings.scan_rest_seconds))

    async def before_action(self, elapsed: float | None = None, on_wait=None) -> float:
        """Wait out the action gap, minus `elapsed` seconds already passed since the last action.

        Returns the seconds actually waited. `on_wait(seconds)` is called before a wait of
        5 seconds or more so the UI can say what is happening.
        """
        delay = self._human(*self.settings.action_delay_seconds)
        if self._rng.random() < 0.08:  # a longer break now and then (1 to 4 typical gaps)
            delay += self._rng.uniform(1, 4) * sum(self.settings.action_delay_seconds) / 2
        wait = delay if elapsed is None else max(0.0, delay - elapsed)
        if wait >= 5 and on_wait is not None:
            on_wait(wait)
        await self._sleep_until_interrupted(wait)
        return wait
