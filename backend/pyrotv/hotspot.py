from __future__ import annotations

import asyncio
import json
import logging
import secrets
import subprocess
import threading
import time
from typing import Callable

import segno

from .config import Config
from .store import Store

log = logging.getLogger(__name__)

CONNECTION = "pyrotv-hotspot"
DEFAULT_SSID = "PyroTV"
DEFAULT_AUTOOFF_MINUTES = 30


class HotspotError(Exception):
    pass


def _default_gen_pin() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(8))


def run_nmcli(args: list[str]) -> int:
    try:
        return subprocess.run(args, capture_output=True, timeout=20).returncode
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        log.warning("command %s failed: %s", args[:3], e)
        return 1


_WIFI_SPECIAL = str.maketrans({c: "\\" + c for c in '\\;,:"'})


def wifi_payload(ssid: str, pin: str) -> str:
    return f"WIFI:T:WPA;S:{ssid.translate(_WIFI_SPECIAL)};P:{pin.translate(_WIFI_SPECIAL)};;"


class Hotspot:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        run: Callable[[list[str]], int],
        now: Callable[[], float] = time.monotonic,
        gen_pin: Callable[[], str] = _default_gen_pin,
    ) -> None:
        self._cfg = cfg
        self._store = store
        self._run = run
        self._now = now
        self._gen_pin = gen_pin
        self._lock = threading.Lock()
        self._pin: str | None = None
        self._is_up = False
        self._up_since = 0.0
        self._last_activity = 0.0

    def touch(self) -> None:
        """Record hotspot client activity. Lock-free: called on the event loop."""
        self._last_activity = self._now()

    @property
    def ssid(self) -> str:
        try:
            data = json.loads(self._cfg.hotspot_file.read_text(encoding="utf-8"))
            ssid = data["ssid"]
        except (OSError, ValueError, KeyError, TypeError):
            return DEFAULT_SSID
        return ssid if isinstance(ssid, str) and ssid else DEFAULT_SSID

    @property
    def pin(self) -> str | None:
        with self._lock:
            return self._pin

    @property
    def is_up(self) -> bool:
        with self._lock:
            return self._is_up

    def up(self) -> None:
        pin = self._gen_pin()
        with self._lock:
            self._pin = None
            self._is_up = False
            if self._run(["nmcli", "con", "modify", CONNECTION, "wifi-sec.psk", pin]) != 0:
                raise HotspotError("could not set the hotspot password")
            if self._run(["nmcli", "con", "up", CONNECTION]) != 0:
                # A half-activated connection would keep the new password live.
                self._run(["nmcli", "con", "down", CONNECTION])
                raise HotspotError("could not start the hotspot")
            self._pin = pin
            self._is_up = True
            self._up_since = self._now()

    def _down_locked(self) -> None:
        rc = self._run(["nmcli", "con", "down", CONNECTION])
        if rc != 0:
            log.warning("nmcli con down exited with %s", rc)
        self._pin = None
        self._is_up = False

    def down(self) -> None:
        with self._lock:
            self._down_locked()

    def qr_svg(self) -> str | None:
        pin = self.pin
        if pin is None:
            return None
        qr = segno.make(wifi_payload(self.ssid, pin), error="m")
        return str(qr.svg_inline(scale=8, border=2))

    def _autooff_minutes(self) -> int:
        raw = self._store.get_setting("hotspot_autooff_minutes", str(DEFAULT_AUTOOFF_MINUTES))
        try:
            return int(raw)
        except (TypeError, ValueError):
            return DEFAULT_AUTOOFF_MINUTES

    def _autooff_check_and_down(self) -> bool:
        """Blocking: check and (if due) switch off under one lock acquisition."""
        with self._lock:
            if not self._is_up:
                return False
            minutes = self._autooff_minutes()
            idle_since = max(self._up_since, self._last_activity)
            if minutes <= 0 or self._now() - idle_since < minutes * 60:
                return False
            self._down_locked()
            return True

    async def autooff_tick(self) -> bool:
        # The lock may be held across slow nmcli calls: never take it on the loop.
        return await asyncio.to_thread(self._autooff_check_and_down)

    async def run_autooff(self, interval: float = 30.0) -> None:
        while True:
            try:
                await self.autooff_tick()
            except Exception:
                log.exception("hotspot auto-off failed")
            await asyncio.sleep(interval)
