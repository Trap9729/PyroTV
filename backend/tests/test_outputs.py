import asyncio
from dataclasses import replace
from pathlib import Path

from pyrotv.config import Config
from pyrotv.events import EventBus
from pyrotv.outputs import OutputWatcher, apply_audio, run_command, tv_connected


def make_cfg(tmp_path: Path, **kw) -> Config:
    return Config(
        data_dir=tmp_path,
        catalogue_path=tmp_path / "c.json",
        static_dir=tmp_path,
        **kw,
    )


def write_status(root: Path, name: str, text: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "status").write_text(text)


def test_tv_connected_true(tmp_path):
    write_status(tmp_path, "card1-HDMI-A-2", "connected\n")
    assert tv_connected("HDMI-A-2", tmp_path) is True


def test_tv_connected_false(tmp_path):
    write_status(tmp_path, "card1-HDMI-A-2", "disconnected\n")
    assert tv_connected("HDMI-A-2", tmp_path) is False


def test_tv_connected_missing(tmp_path):
    assert tv_connected("HDMI-A-2", tmp_path) is False
    assert tv_connected("HDMI-A-2", tmp_path / "nope") is False


def test_tv_connected_unreadable_status(tmp_path):
    (tmp_path / "card1-HDMI-A-2" / "status").mkdir(parents=True)
    assert tv_connected("HDMI-A-2", tmp_path) is False


def test_apply_audio_routes_sink(tmp_path):
    cfg = make_cfg(tmp_path, sink_hdmi="hdmi-sink", sink_jack="jack-sink")
    calls: list[list[str]] = []
    apply_audio(True, cfg, calls.append)
    apply_audio(False, cfg, calls.append)
    assert calls == [
        ["pactl", "set-default-sink", "hdmi-sink"],
        ["pactl", "set-default-sink", "jack-sink"],
    ]


def test_apply_audio_noop_in_fake_hw(tmp_path):
    cfg = make_cfg(tmp_path, sink_hdmi="h", sink_jack="j", fake_hw=True)
    calls: list[list[str]] = []
    apply_audio(True, cfg, calls.append)
    assert calls == []


def test_apply_audio_noop_when_sink_unset(tmp_path):
    cfg = make_cfg(tmp_path, sink_hdmi="", sink_jack="j")
    calls: list[list[str]] = []
    apply_audio(True, cfg, calls.append)
    assert calls == []
    apply_audio(False, replace(cfg, sink_jack=""), calls.append)
    assert calls == []


def test_run_command_missing_executable_does_not_raise():
    run_command(["definitely-not-a-real-executable-xyz"])


def _seq_probe(values):
    it = iter(values)
    last = values[-1]

    def probe():
        return next(it, last)

    return probe


async def test_watcher_publishes_changes_in_order(tmp_path):
    bus = EventBus()
    q = bus.subscribe()
    w = OutputWatcher(
        make_cfg(tmp_path), bus, interval=0.01,
        probe=_seq_probe([False, True, True, False, False]),
    )
    assert w.connected is False
    task = asyncio.create_task(w.run())
    first = await asyncio.wait_for(q.get(), 2)
    second = await asyncio.wait_for(q.get(), 2)
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert first == {"type": "tv", "connected": True}
    assert second == {"type": "tv", "connected": False}
    assert q.empty()
    assert w.connected is False


async def test_watcher_fake_mode_uses_fake_tv_file(tmp_path):
    bus = EventBus()
    q = bus.subscribe()
    w = OutputWatcher(make_cfg(tmp_path, fake_hw=True), bus, interval=0.01)
    task = asyncio.create_task(w.run())
    await asyncio.sleep(0.05)
    assert q.empty()
    (tmp_path / "fake_tv").write_text("")
    assert await asyncio.wait_for(q.get(), 2) == {"type": "tv", "connected": True}
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
