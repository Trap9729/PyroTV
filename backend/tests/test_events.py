import asyncio

from pyrotv.events import EventBus


def test_delivers_to_every_subscriber():
    bus = EventBus()
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish({"type": "x"})
    assert a.get_nowait() == {"type": "x"}
    assert b.get_nowait() == {"type": "x"}


def test_unsubscribe_stops_delivery_and_unknown_is_noop():
    bus = EventBus()
    a = bus.subscribe()
    bus.unsubscribe(a)
    bus.unsubscribe(a)
    bus.unsubscribe(asyncio.Queue())
    bus.publish({"type": "x"})
    assert a.empty()


def test_full_queue_drops_without_affecting_others():
    bus = EventBus()
    slow, fast = bus.subscribe(), bus.subscribe()
    for i in range(100):
        bus.publish({"i": i})
    fast_items = [fast.get_nowait() for _ in range(100)]
    assert len(fast_items) == 100
    bus.publish({"i": 100})
    assert slow.qsize() == 100
    assert fast.get_nowait() == {"i": 100}
