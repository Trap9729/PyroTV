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
    proc = DummyProc()
    servers = []

    async def spawn(args):
        # like real mpv, create the IPC socket after launch
        servers.append(await asyncio.start_unix_server(handle, path=str(cfg.mpv_socket)))
        return proc

    player = MpvPlayer(cfg, spawn=spawn)
    try:
        await player.play(Path("v.mp4"))
        await player.seek(10)
        assert {"command": ["seek", 10, "relative"]} in received
        assert await player.status() == PlayerStatus("playing", 12.5, 99.0)
        await player.stop()
        assert proc.returncode is not None
    finally:
        for server in servers:
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


async def test_mpv_concurrent_play_leaves_one_process(tmp_path):
    procs: list[DummyProc] = []

    async def spawn(args):
        await asyncio.sleep(0)
        p = DummyProc()
        procs.append(p)
        return p

    player = MpvPlayer(make_cfg(tmp_path), spawn=spawn)
    await asyncio.gather(player.play(Path("a.mp4")), player.play(Path("b.mp4")))
    assert len(procs) == 2
    assert procs[0].returncode is not None
    assert procs[1].returncode is None
    await player.stop()
    assert procs[1].returncode is not None


async def test_mpv_stop_tolerates_process_lookup_error(tmp_path):
    class Gone(DummyProc):
        def terminate(self):
            raise ProcessLookupError

        async def wait(self):
            await asyncio.sleep(10)

    player = MpvPlayer(make_cfg(tmp_path), spawn=lambda args: _async_value(Gone()))
    await player.play(Path("a.mp4"))
    await player.stop()
