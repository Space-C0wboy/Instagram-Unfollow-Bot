from igcleanup.jobs.progress import ProgressBus


async def test_publish_reaches_all_subscribers_and_records_last():
    bus = ProgressBus()
    q1, q2 = bus.subscribe(), bus.subscribe()
    bus.publish({"state": "running", "done": 1})
    assert q1.get_nowait() == {"state": "running", "done": 1}
    assert q2.get_nowait() == {"state": "running", "done": 1}
    assert bus.last == {"state": "running", "done": 1}
    bus.unsubscribe(q2)
    bus.publish({"state": "done"})
    assert q1.get_nowait() == {"state": "done"}
    assert q2.empty()
