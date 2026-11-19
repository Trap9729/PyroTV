from __future__ import annotations

import asyncio
import itertools
import json

import pytest

from pyrotv.hotspot import Hotspot, HotspotError, wifi_payload
from pyrotv.store import Store


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def store(cfg):
    cfg.ensure_dirs()
    s = Store(cfg.db_path)
    yield s
    s.close()


def make(cfg, store, rc=0, **kw):
    calls: list[list[str]] = []

    def run(args):
        calls.append(list(args))
        return rc

    return Hotspot(cfg, store, run, **kw), calls


def test_up_runs_modify_then_up(cfg, store):
    h, calls = make(cfg, store, gen_pin=lambda: "00123456")
    h.up()
    assert calls == [
        ["nmcli", "con", "modify", "pyrotv-hotspot", "wifi-sec.psk", "00123456"],
        ["nmcli", "con", "up", "pyrotv-hotspot"],
    ]
    assert h.is_up and h.pin == "00123456"


def test_default_pin_is_8_digits_and_changes(cfg, store):
    h, _ = make(cfg, store)
    h.up()
    p1 = h.pin
    h.down()
    assert h.pin is None
    seen = {p1}
    for _ in range(5):
        h.up()
        assert len(h.pin) == 8 and h.pin.isdigit()
        seen.add(h.pin)
        h.down()
    assert len(seen) > 1


def test_up_twice_changes_pin(cfg, store):
    counter = itertools.count(10000000)
    h, _ = make(cfg, store, gen_pin=lambda: str(next(counter)))
    h.up()
    first = h.pin
    h.up()
    assert h.pin != first and h.is_up


def test_down_runs_command_clears_pin_idempotent(cfg, store):
    h, calls = make(cfg, store, gen_pin=lambda: "11111111")
    h.up()
    calls.clear()
    h.down()
    assert calls == [["nmcli", "con", "down", "pyrotv-hotspot"]]
    assert h.pin is None and not h.is_up
    h.down()
    assert not h.is_up


def test_failure_raises_and_stays_down(cfg, store):
    h, _ = make(cfg, store, rc=1)
    with pytest.raises(HotspotError):
        h.up()
    assert not h.is_up and h.pin is None


def test_failed_con_up_takes_connection_down(cfg, store):
    calls: list[list[str]] = []

    def run(args):
        calls.append(list(args))
        return 1 if args[2] == "up" else 0

    h = Hotspot(cfg, store, run, gen_pin=lambda: "00123456")
    with pytest.raises(HotspotError):
        h.up()
    assert calls[-1] == ["nmcli", "con", "down", "pyrotv-hotspot"]
    assert not h.is_up and h.pin is None


def test_down_failure_not_raised(cfg, store):
    h, _ = make(cfg, store)
    h.up()
    h._run = lambda args: 1
    h.down()
    assert not h.is_up and h.pin is None


def test_ssid(cfg, store):
    h, _ = make(cfg, store)
    assert h.ssid == "PyroTV"
    cfg.hotspot_file.write_text(json.dumps({"ssid": "PyroTV-AB12"}))
    assert h.ssid == "PyroTV-AB12"
    cfg.hotspot_file.write_text("garbage")
    assert h.ssid == "PyroTV"


def test_wifi_payload():
    assert wifi_payload("PyroTV", "12345678") == "WIFI:T:WPA;S:PyroTV;P:12345678;;"
    assert wifi_payload('a;b,c:d"e\\f', "1") == 'WIFI:T:WPA;S:a\\;b\\,c\\:d\\"e\\\\f;P:1;;'


def test_qr_svg(cfg, store):
    h, _ = make(cfg, store)
    assert h.qr_svg() is None
    h.up()
    svg = h.qr_svg()
    assert isinstance(svg, str) and svg.startswith("<svg")


async def test_autooff_after_timeout(cfg, store):
    clock = Clock()
    h, calls = make(cfg, store, now=clock)
    h.up()
    store.set_setting("hotspot_autooff_minutes", "30")
    clock.t += 29 * 60
    assert await h.autooff_tick() is False and h.is_up
    clock.t += 2 * 60
    calls.clear()
    assert await h.autooff_tick() is True
    assert calls == [["nmcli", "con", "down", "pyrotv-hotspot"]]
    assert not h.is_up
    assert await h.autooff_tick() is False


async def test_autooff_default_and_invalid_are_30(cfg, store):
    clock = Clock()
    h, _ = make(cfg, store, now=clock)
    h.up()
    clock.t += 31 * 60
    assert await h.autooff_tick() is True  # unset -> 30
    h.up()
    store.set_setting("hotspot_autooff_minutes", "abc")
    clock.t += 29 * 60
    assert await h.autooff_tick() is False
    clock.t += 2 * 60
    assert await h.autooff_tick() is True


async def test_autooff_zero_never(cfg, store):
    clock = Clock()
    h, calls = make(cfg, store, now=clock)
    h.up()
    store.set_setting("hotspot_autooff_minutes", "0")
    clock.t += 10 * 24 * 3600
    calls.clear()
    assert await h.autooff_tick() is False
    assert calls == [] and h.is_up


async def test_autooff_tick_does_not_block_event_loop(cfg, store):
    h, _ = make(cfg, store)
    h.up()
    h._lock.acquire()  # simulate up() stuck in a slow nmcli call
    try:
        tick = asyncio.create_task(h.autooff_tick())
        flag = []

        async def other():
            await asyncio.sleep(0.05)
            flag.append(1)

        await asyncio.wait_for(other(), timeout=2)
        assert flag == [1] and not tick.done()
    finally:
        h._lock.release()
    assert await asyncio.wait_for(tick, timeout=2) is False


async def test_autooff_tick_sees_new_up_since_after_reapply(cfg, store):
    clock = Clock()
    h, calls = make(cfg, store, now=clock)
    h.up()
    clock.t += 31 * 60  # old switch-on is past the limit
    h.up()  # switched on again: timer restarts
    calls.clear()
    assert await h.autooff_tick() is False
    assert h.is_up and calls == []


async def test_client_activity_postpones_autooff(cfg, store):
    clock = Clock()
    h, calls = make(cfg, store, now=clock)
    h.up()
    store.set_setting("hotspot_autooff_minutes", "30")
    clock.t += 20 * 60
    h.touch()  # a phone is still using the hotspot
    clock.t += 20 * 60  # 40 min since switch-on, 20 min idle
    assert await h.autooff_tick() is False and h.is_up
    clock.t += 11 * 60  # 31 min idle
    assert await h.autooff_tick() is True and not h.is_up


async def test_run_autooff_loop_cancels(cfg, store):
    h, _ = make(cfg, store)
    t = asyncio.create_task(h.run_autooff(interval=0.01))
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
