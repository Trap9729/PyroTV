from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Callable

from .config import Config
from .events import EventBus

log = logging.getLogger(__name__)


def tv_connected(connector: str, drm_root: Path = Path("/sys/class/drm")) -> bool:
    try:
        for d in drm_root.glob(f"card*-{connector}"):
            try:
                if (d / "status").read_text().strip() == "connected":
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


def run_command(args: list[str]) -> None:
    try:
        subprocess.run(args, check=False, timeout=5, capture_output=True)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("command %s failed: %s", args, e)


def apply_audio(tv: bool, cfg: Config, run: Callable[[list[str]], None]) -> None:
    sink = cfg.sink_hdmi if tv else cfg.sink_jack
    if cfg.fake_hw or not sink:
        return
    run(["pactl", "set-default-sink", sink])


class OutputWatcher:
    def __init__(
        self,
        cfg: Config,
        bus: EventBus,
        interval: float = 2.0,
        probe: Callable[[], bool] | None = None,
    ) -> None:
        self._cfg = cfg
        self._bus = bus
        self._interval = interval
        self._probe = probe or self._default_probe
        self.connected = False

    def _default_probe(self) -> bool:
        if self._cfg.fake_hw:
            return (self._cfg.data_dir / "fake_tv").exists()
        return tv_connected(self._cfg.tv_connector)

    async def run(self) -> None:
        while True:
            now = await asyncio.to_thread(self._probe)
            if now != self.connected:
                self.connected = now
                self._bus.publish({"type": "tv", "connected": now})
            await asyncio.sleep(self._interval)
