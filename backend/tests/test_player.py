import asyncio
import json
import sys
from pathlib import Path

import pytest

from pyrotv.config import Config
from pyrotv.player import FakePlayer, MpvPlayer, PlayerStatus


def make_cfg(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path, catalogue_path=tmp_path / "c.json", static_dir=tmp_path)


async def test_fake_play_seek_clamp_end():
    p = FakePlayer(duration=60.0)
    assert (await p.status()).state == "idle"
    await p.play(Path("x.mp4"))
    assert await p.status() == PlayerStatus("playing", 0.0, 60.0)
    p.tick(5)
    await p.seek(10)
    assert (await p.status()).position == 15
    await p.seek(-100)
    assert (await p.status()).position == 0
    await p.seek(999)
    assert (await p.status()).state == "idle"


async def test_fake_pause_tick_stop():
    p = FakePlayer(duration=10.0)
    await p.toggle_pause()
    assert (await p.status()).state == "idle"
    await p.play(Path("x.mp4"))
    await p.toggle_pause()
    p.tick(3)
    assert await p.status() == PlayerStatus("paused", 0.0, 10.0)
    await p.toggle_pause()
    p.tick(3)
    assert (await p.status()).position == 3
    p.tick(10)
    assert (await p.status()).state == "idle"
    await p.play(Path("x.mp4"))
    await p.stop()
    assert (await p.status()).state == "idle"


class DummyProc:
    returncode = None

    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    async def wait(self):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


async def _async_value(v):
    return v


@pytest.mark.skipif(sys.platform == "win32", reason="needs unix sockets")
async def test_mpv_player_ipc(tmp_path):
    received: list[dict] = []

    async def handle(reader, writer):
        while line := await reader.readline():
            msg = json.loads(line)
            received.append(msg)
            cmd = msg["command"]
            writer.write(b'{"event": "noise"}\n')
            if cmd[0] == "get_property":
                data = {"time-pos": 12.5, "duration": 99.0, "pause": False}[cmd[1]]
                writer.write(json.dumps({"data": data, "error": "success"}).encode() + b"\n")
            else:
                writer.write(b'{"error": "success"}\n')
            await writer.drain()
        writer.close()

    cfg = make_cfg(tmp_path)
    server = await asyncio.start_unix_server(handle, path=str(cfg.mpv_socket))
    proc = DummyProc()
    player = MpvPlayer(cfg, spawn=lambda args: _async_value(proc))
    try:
        await player.play(Path("v.mp4"))
        await player.seek(10)
        assert {"command": ["seek", 10, "relative"]} in received
        assert await player.status() == PlayerStatus("playing", 12.5, 99.0)
        await player.stop()
        assert proc.returncode is not None
    finally:
        server.close()
        await server.wait_closed()


async def test_mpv_status_idle_without_process(tmp_path):
    player = MpvPlayer(make_cfg(tmp_path))
    assert await player.status() == PlayerStatus("idle", 0.0, 0.0)


async def test_mpv_status_playing_when_socket_not_ready(tmp_path):
    player = MpvPlayer(make_cfg(tmp_path), spawn=lambda args: _async_value(DummyProc()))
    await player.play(Path("v.mp4"))
    assert await player.status() == PlayerStatus("playing", 0.0, 0.0)
    await player.stop()
    assert await player.status() == PlayerStatus("idle", 0.0, 0.0)
