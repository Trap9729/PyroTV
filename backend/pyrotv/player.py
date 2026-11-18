from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Protocol

from .config import Config

log = logging.getLogger(__name__)

State = Literal["idle", "playing", "paused"]


@dataclass(frozen=True)
class PlayerStatus:
    state: State
    position: float
    duration: float


class Player(Protocol):
    async def play(self, path: Path) -> None: ...

    async def seek(self, delta: float) -> None: ...

    async def toggle_pause(self) -> None: ...

    async def stop(self) -> None: ...

    async def status(self) -> PlayerStatus: ...


class FakePlayer:
    """In-memory player; position only advances via tick()."""

    def __init__(self, duration: float = 60.0) -> None:
        self._duration = duration
        self._state: State = "idle"
        self._position = 0.0
        self.last_path: Path | None = None

    def _end(self) -> None:
        self._state = "idle"
        self._position = 0.0

    async def play(self, path: Path) -> None:
        self.last_path = path
        self._state = "playing"
        self._position = 0.0

    async def seek(self, delta: float) -> None:
        if self._state == "idle":
            return
        self._position = max(0.0, self._position + delta)
        if self._position >= self._duration:
            self._end()

    async def toggle_pause(self) -> None:
        if self._state == "playing":
            self._state = "paused"
        elif self._state == "paused":
            self._state = "playing"

    async def stop(self) -> None:
        self._end()

    async def status(self) -> PlayerStatus:
        if self._state == "idle":
            return PlayerStatus("idle", 0.0, 0.0)
        return PlayerStatus(self._state, self._position, self._duration)

    def tick(self, seconds: float) -> None:
        if self._state != "playing":
            return
        self._position += seconds
        if self._position >= self._duration:
            self._end()


async def _spawn_mpv(args: list[str]) -> Any:
    return await asyncio.create_subprocess_exec(
        *args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class MpvPlayer:
    def __init__(
        self,
        cfg: Config,
        spawn: Callable[[list[str]], Awaitable[Any]] = _spawn_mpv,
    ) -> None:
        self._cfg = cfg
        self._spawn = spawn
        self._proc: Any = None

    async def play(self, path: Path) -> None:
        await self.stop()
        sock = self._cfg.mpv_socket
        try:
            sock.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("could not remove stale mpv socket: %s", e)
        args = [
            "mpv",
            "--no-terminal",
            "--fs",
            f"--fs-screen-name={self._cfg.tv_connector}",
            "--keep-open=no",
            f"--input-ipc-server={sock}",
            *self._cfg.mpv_args,
            str(path),
        ]
        self._proc = await self._spawn(args)

    async def _ipc(self, command: list[Any], timeout: float = 1.0) -> Any:
        """Send one command; return the reply's data. Raises on any failure."""

        async def go() -> Any:
            reader, writer = await asyncio.open_unix_connection(str(self._cfg.mpv_socket))
            try:
                writer.write(json.dumps({"command": command}).encode() + b"\n")
                await writer.drain()
                while True:
                    line = await reader.readline()
                    if not line:
                        raise ConnectionError("mpv closed the socket")
                    msg = json.loads(line)
                    if "error" in msg:
                        if msg["error"] != "success":
                            raise RuntimeError(f"mpv error: {msg['error']}")
                        return msg.get("data")
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass

        return await asyncio.wait_for(go(), timeout)

    async def _try_ipc(self, command: list[Any]) -> None:
        try:
            await self._ipc(command)
        except (OSError, RuntimeError, ValueError, asyncio.TimeoutError, AttributeError) as e:
            log.warning("mpv command %s failed: %s", command, e)

    def _running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def seek(self, delta: float) -> None:
        if self._running():
            await self._try_ipc(["seek", delta, "relative"])

    async def toggle_pause(self) -> None:
        if self._running():
            await self._try_ipc(["cycle", "pause"])

    async def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            return
        await self._try_ipc(["quit"])
        try:
            await asyncio.wait_for(proc.wait(), 1.0)
            return
        except asyncio.TimeoutError:
            pass
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 2.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    async def status(self) -> PlayerStatus:
        if not self._running():
            return PlayerStatus("idle", 0.0, 0.0)
        try:
            pos = await self._ipc(["get_property", "time-pos"])
            dur = await self._ipc(["get_property", "duration"])
            paused = await self._ipc(["get_property", "pause"])
            return PlayerStatus(
                "paused" if paused else "playing", float(pos or 0.0), float(dur or 0.0)
            )
        except Exception:  # socket not ready yet or any IPC error; never raise
            return PlayerStatus("playing", 0.0, 0.0)
