import asyncio


class ProgressBus:
    def __init__(self):
        self._subs: list[asyncio.Queue] = []
        self.last: dict | None = None

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subs:
            self._subs.remove(q)

    def publish(self, event: dict) -> None:
        self.last = event
        for q in self._subs:
            q.put_nowait(event)
