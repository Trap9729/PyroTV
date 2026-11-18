from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Literal

from .config import Config
from .events import EventBus
from .library import Library
from .outputs import OutputWatcher, apply_audio, run_command
from .player import Player

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlayStart:
    mode: Literal["local", "tv"]
    title: str
    key: str


class PlaybackService:
    def __init__(
        self,
        library: Library,
        outputs: OutputWatcher,
        player: Player,
        bus: EventBus,
        cfg: Config,
        poll_interval: float = 0.5,
        *,
        run: Callable[[list[str]], None] = run_command,
    ) -> None:
        self._library = library
        self._outputs = outputs
        self._player = player
        self._bus = bus
        self._cfg = cfg
        self._poll_interval = poll_interval
        self._run = run
        self._tv_key: str | None = None
        self._lock = asyncio.Lock()

    async def start(self, key: str) -> PlayStart:
        async with self._lock:
            return await self._start_locked(key)

    async def command(self, cmd: Literal["seek", "pause", "stop"], delta: float = 0) -> None:
        async with self._lock:
            await self._command_locked(cmd, delta)

    async def poll_once(self) -> None:
        async with self._lock:
            await self._poll_locked()

    async def _start_locked(self, key: str) -> PlayStart:
        piece = self._library.get(key)
        if piece is None or piece.video is None:
            raise KeyError(key)
        if self._outputs.connected:
            apply_audio(True, self._cfg, self._run)
            await self._player.play(piece.video)
            self._tv_key = key
            return PlayStart(mode="tv", title=piece.name, key=key)
        if self._tv_key is not None:
            await self._player.stop()
            self._tv_key = None
        apply_audio(False, self._cfg, self._run)
        return PlayStart(mode="local", title=piece.name, key=key)

    async def _command_locked(self, cmd: str, delta: float) -> None:
        if self._tv_key is None:
            return
        if cmd == "seek":
            await self._player.seek(delta)
        elif cmd == "pause":
            await self._player.toggle_pause()
        elif cmd == "stop":
            await self._player.stop()
            self._tv_key = None
            self._bus.publish({"type": "stopped", "reason": "user"})

    async def _poll_locked(self) -> None:
        if self._tv_key is None:
            return
        if not self._outputs.connected:
            try:
                await self._player.stop()
            finally:
                apply_audio(False, self._cfg, self._run)
                self._tv_key = None
                self._bus.publish({"type": "stopped", "reason": "tv_disconnected"})
            return
        st = await self._player.status()
        if st.state == "idle":
            self._tv_key = None
            self._bus.publish({"type": "stopped", "reason": "ended"})
            return
        self._bus.publish(
            {
                "type": "player",
                "state": st.state,
                "position": st.position,
                "duration": st.duration,
            }
        )

    async def run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.exception("playback poll failed")
            await asyncio.sleep(self._poll_interval)
