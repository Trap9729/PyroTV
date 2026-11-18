import asyncio
from pathlib import Path

import pytest

from pyrotv.catalogue import Piece
from pyrotv.config import Config
from pyrotv.events import EventBus
from pyrotv.player import FakePlayer
from pyrotv.playback import PlaybackService, PlayStart


class StubLibrary:
    def __init__(self, pieces):
        self._p = {p.key: p for p in pieces}

    def get(self, key):
        return self._p.get(key)


class StubOutputs:
    def __init__(self, connected=False):
        self.connected = connected


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@pytest.fixture
def env(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        catalogue_path=tmp_path / "c.json",
        static_dir=tmp_path,
        sink_hdmi="hdmi",
        sink_jack="jack",
    )
    lib = StubLibrary(
        [
            Piece("a-x", "X", "a", "cat", Path("x.mp4"), None),
            Piece("a-novid", "NoVid", "a", "cat", None, None),
        ]
    )
    outputs = StubOutputs()
    player = FakePlayer(duration=10.0)
    bus = EventBus()
    q = bus.subscribe()
    calls: list[list[str]] = []
    svc = PlaybackService(lib, outputs, player, bus, cfg, run=calls.append)
    return svc, outputs, player, q, calls


async def test_start_local(env):
    svc, outputs, player, q, calls = env
    r = await svc.start("a-x")
    assert r == PlayStart(mode="local", title="X", key="a-x")
    assert (await player.status()).state == "idle"
    assert calls == [["pactl", "set-default-sink", "jack"]]


async def test_start_tv(env):
    svc, outputs, player, q, calls = env
    outputs.connected = True
    r = await svc.start("a-x")
    assert r.mode == "tv"
    assert (await player.status()).state == "playing"
    assert calls == [["pactl", "set-default-sink", "hdmi"]]


async def test_unknown_key(env):
    svc, *_ = env
    with pytest.raises(KeyError):
        await svc.start("nope")
    with pytest.raises(KeyError):
        await svc.start("a-novid")


async def test_tv_disconnect_stops(env):
    svc, outputs, player, q, calls = env
    outputs.connected = True
    await svc.start("a-x")
    outputs.connected = False
    await svc.poll_once()
    assert (await player.status()).state == "idle"
    assert calls[-1] == ["pactl", "set-default-sink", "jack"]
    assert drain(q) == [{"type": "stopped", "reason": "tv_disconnected"}]
    await svc.poll_once()
    assert drain(q) == []


async def test_poll_publishes_and_ends(env):
    svc, outputs, player, q, calls = env
    outputs.connected = True
    await svc.start("a-x")
    player.tick(4)
    await svc.poll_once()
    assert drain(q) == [
        {"type": "player", "state": "playing", "position": 4.0, "duration": 10.0}
    ]
    player.tick(100)
    await svc.poll_once()
    assert drain(q) == [{"type": "stopped", "reason": "ended"}]
    await svc.poll_once()
    assert drain(q) == []


async def test_tv_connecting_during_local_changes_nothing(env):
    svc, outputs, player, q, calls = env
    await svc.start("a-x")
    outputs.connected = True
    await svc.poll_once()
    assert (await player.status()).state == "idle"
    assert drain(q) == []


async def test_commands(env):
    svc, outputs, player, q, calls = env
    await svc.command("pause")  # no TV playback: ignored
    outputs.connected = True
    await svc.start("a-x")
    await svc.command("seek", 5)
    assert (await player.status()).position == 5
    await svc.command("pause")
    assert (await player.status()).state == "paused"
    await svc.command("stop")
    assert (await player.status()).state == "idle"
    assert drain(q) == [{"type": "stopped", "reason": "user"}]
    await svc.command("seek", 5)
    assert (await player.status()).position == 0


async def test_start_local_stops_active_tv(env):
    svc, outputs, player, q, calls = env
    outputs.connected = True
    await svc.start("a-x")
    outputs.connected = False
    r = await svc.start("a-x")
    assert r.mode == "local"
    assert (await player.status()).state == "idle"


async def test_disconnect_with_failing_stop_still_cleans_up(env):
    svc, outputs, player, q, calls = env
    outputs.connected = True
    await svc.start("a-x")

    async def boom():
        raise RuntimeError("stop failed")

    player.stop = boom
    outputs.connected = False
    with pytest.raises(RuntimeError):
        await svc.poll_once()
    assert calls[-1] == ["pactl", "set-default-sink", "jack"]
    assert drain(q) == [{"type": "stopped", "reason": "tv_disconnected"}]
    await svc.poll_once()  # _tv_key cleared: nothing more happens
    assert drain(q) == []


async def test_run_survives_poll_exception(env):
    svc, outputs, player, q, calls = env
    n = 0
    orig = svc.poll_once

    async def flaky():
        nonlocal n
        n += 1
        if n == 1:
            raise RuntimeError("boom")
        await orig()

    svc.poll_once = flaky
    svc._poll_interval = 0.001
    task = asyncio.create_task(svc.run())
    try:
        await asyncio.wait_for(_until(lambda: n >= 3), 2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def _until(pred):
    while not pred():
        await asyncio.sleep(0.001)


class SlowSwitchPlayer(FakePlayer):
    """Like MpvPlayer.play: stop the old process, yield, then start the new one."""

    async def play(self, path):
        await self.stop()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await super().play(path)

    async def stop(self):
        await super().stop()
        await asyncio.sleep(0)


@pytest.fixture
def slow_env(env):
    svc, outputs, _player, q, calls = env
    svc._library._p["a-y"] = Piece("a-y", "Y", "a", "cat", Path("y.mp4"), None)
    player = SlowSwitchPlayer(duration=10.0)
    svc._player = player
    outputs.connected = True
    return svc, player, q


async def test_tile_switch_not_seen_as_ended_by_poll(slow_env):
    svc, player, q = slow_env
    await svc.start("a-x")
    await asyncio.gather(svc.start("a-y"), svc.poll_once())
    assert [e for e in drain(q) if e["type"] == "stopped"] == []
    assert svc._tv_key == "a-y"
    assert (await player.status()).state == "playing"


async def test_stop_racing_poll_publishes_one_stopped(slow_env):
    svc, player, q = slow_env
    await svc.start("a-x")
    await asyncio.gather(svc.command("stop"), svc.poll_once())
    stopped = [e for e in drain(q) if e["type"] == "stopped"]
    assert stopped == [{"type": "stopped", "reason": "user"}]
