from __future__ import annotations

import json
import os

import pytest

from pyrotv.backup import export_backup, find_usb_mount, import_backup
from pyrotv.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "a.db")
    yield s
    s.close()


def seed(s: Store) -> None:
    s.set_setting("shop_name", "Brandkåren")
    s.set_enabled_manufacturers(["lesli"])
    s.set_piece_enabled("lesli:Blixt", False)
    s.add_view("lesli:Åska 49")
    s.flush_views()


def test_roundtrip(store, tmp_path):
    seed(store)
    dest = tmp_path / "usb"
    dest.mkdir()
    p = export_backup(store, dest)
    assert p == dest / "pyrotv-backup.json"
    assert json.loads(p.read_text("utf-8"))["version"] == 1
    assert not (dest / "pyrotv-backup.json.part").exists()
    other = Store(tmp_path / "b.db")
    try:
        import_backup(other, p)
        assert other.export_state() == store.export_state()
    finally:
        other.close()


def test_partial_backup_never_visible(store, tmp_path, monkeypatch):
    seed(store)
    dest = tmp_path / "usb"
    dest.mkdir()

    def boom(src, dst):
        raise OSError("power cut")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        export_backup(store, dest)
    assert not (dest / "pyrotv-backup.json").exists()


def test_import_rejects_bad_files(store, tmp_path):
    for content in (
        "not json",
        "[]",
        '{"version": 2, "state": {}}',
        '{"version": 1, "state": 5}',
        '{"version": 1, "state": {"settings": []}}',
    ):
        f = tmp_path / "x.json"
        f.write_text(content)
        with pytest.raises(ValueError):
            import_backup(store, f)


def test_find_usb_mount(tmp_path):
    assert find_usb_mount(tmp_path / "missing") is None
    root = tmp_path / "media"
    (root / "pi" / "STICK").mkdir(parents=True)
    (root / "pi" / "EMPTY").mkdir()
    (root / "direct").mkdir()
    assert find_usb_mount(root, is_mount=lambda p: False) is None
    assert find_usb_mount(root, is_mount=lambda p: p.name == "STICK") == root / "pi" / "STICK"
    assert find_usb_mount(root, is_mount=lambda p: p.name == "direct") == root / "direct"
    assert find_usb_mount(root, is_mount=lambda p: True) == root / "pi" / "EMPTY"
