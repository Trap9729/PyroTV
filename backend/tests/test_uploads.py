from __future__ import annotations

from pathlib import Path

import pytest

from pyrotv.config import Config
from pyrotv.uploads import DiskFull, IncompleteUpload, OffsetMismatch, UploadManager

MB = 1024 * 1024
BIG = 1 << 40


def _cfg(tmp_path: Path) -> Config:
    cfg = Config(data_dir=tmp_path / "data", catalogue_path=tmp_path / "c.json", static_dir=tmp_path)
    cfg.ensure_dirs()
    return cfg


def _mgr(tmp_path: Path, free: int = BIG) -> tuple[Config, UploadManager]:
    cfg = _cfg(tmp_path)
    return cfg, UploadManager(cfg, free_bytes=lambda p: free)


def test_three_chunks_assemble(tmp_path):
    cfg, m = _mgr(tmp_path)
    data = bytes(range(256)) * 12  # 3072
    info = m.create("video", "a.mp4", len(data))
    assert info.received == 0 and info.kind == "video" and info.filename == "a.mp4"
    n = 1024
    assert m.write_chunk(info.id, 0, data[:n]) == n
    assert m.write_chunk(info.id, n, data[n : 2 * n]) == 2 * n
    assert m.write_chunk(info.id, 2 * n, data[2 * n :]) == 3 * n
    assert m.status(info.id).received == 3 * n
    assert m.finish(info.id).read_bytes() == data


def test_stale_offset_reports_received(tmp_path):
    _, m = _mgr(tmp_path)
    info = m.create("video", "a.mp4", 100)
    m.write_chunk(info.id, 0, b"x" * 40)
    with pytest.raises(OffsetMismatch) as e:
        m.write_chunk(info.id, 0, b"x" * 40)
    assert e.value.received == 40
    with pytest.raises(OffsetMismatch):
        m.write_chunk(info.id, 50, b"x")


def test_resume_survives_new_manager(tmp_path):
    cfg, m = _mgr(tmp_path)
    info = m.create("video", "a.mp4", 100)
    m.write_chunk(info.id, 0, b"y" * 30)
    m2 = UploadManager(cfg, free_bytes=lambda p: BIG)
    assert m2.status(info.id).received == 30
    assert m2.write_chunk(info.id, 30, b"y" * 70) == 100


def test_disk_full(tmp_path):
    _, m = _mgr(tmp_path, free=500 * MB)
    with pytest.raises(DiskFull):
        m.create("video", "a.mp4", 100 * MB)


def test_finish_incomplete(tmp_path):
    _, m = _mgr(tmp_path)
    info = m.create("video", "a.mp4", 100)
    m.write_chunk(info.id, 0, b"x" * 10)
    with pytest.raises(IncompleteUpload) as e:
        m.finish(info.id)
    assert (e.value.received, e.value.size) == (10, 100)


def test_overshoot_rejected(tmp_path):
    _, m = _mgr(tmp_path)
    info = m.create("video", "a.mp4", 10)
    with pytest.raises(ValueError):
        m.write_chunk(info.id, 0, b"x" * 11)


def test_cleanup_stale(tmp_path):
    cfg, m = _mgr(tmp_path)
    (cfg.upload_tmp_dir / "old.part").write_bytes(b"x")
    info = m.create("image", "a.png", 10)
    assert m.cleanup_stale() >= 2
    assert list(cfg.upload_tmp_dir.iterdir()) == []
    with pytest.raises(KeyError):
        m.status(info.id)


def test_bad_ids_rejected(tmp_path):
    _, m = _mgr(tmp_path)
    for bad in ("../x", "abc", "A" * 32, "../" + "a" * 29):
        with pytest.raises(KeyError):
            m.status(bad)
        with pytest.raises(KeyError):
            m.write_chunk(bad, 0, b"x")
        with pytest.raises(KeyError):
            m.finish(bad)
        m.discard(bad)  # must not raise or touch anything


def test_validation(tmp_path):
    _, m = _mgr(tmp_path)
    with pytest.raises(ValueError, match="type"):
        m.create("video", "a.exe", 10)
    with pytest.raises(ValueError, match="type"):
        m.create("image", "a.mp4", 10)
    with pytest.raises(ValueError, match="size"):
        m.create("video", "a.mp4", 0)
    with pytest.raises(ValueError, match="size"):
        m.create("video", "a.mp4", 4 * 1024**3 + 1)
    with pytest.raises(ValueError, match="size"):
        m.create("image", "a.png", 50 * MB + 1)
    assert m.create("video", "A.MOV", 10).filename == "A.MOV"
    assert m.create("image", "a.JPEG", 10).kind == "image"


def test_discard(tmp_path):
    cfg, m = _mgr(tmp_path)
    info = m.create("video", "a.mp4", 10)
    m.discard(info.id)
    assert list(cfg.upload_tmp_dir.iterdir()) == []


def test_concurrent_same_offset_exactly_one_wins(tmp_path):
    import threading

    _, m = _mgr(tmp_path)
    info = m.create("video", "a.mp4", 100)
    barrier = threading.Barrier(2)
    results = []

    def go():
        barrier.wait()
        try:
            results.append(m.write_chunk(info.id, 0, b"z" * 60))
        except OffsetMismatch as e:
            results.append(e)

    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(type(r).__name__ for r in results) == ["OffsetMismatch", "int"]
    assert m.status(info.id).received == 60


def test_concurrent_different_uploads(tmp_path):
    import threading

    _, m = _mgr(tmp_path)
    a, b = m.create("video", "a.mp4", 50), m.create("video", "b.mp4", 50)
    barrier = threading.Barrier(2)

    def go(uid, byte):
        barrier.wait()
        m.write_chunk(uid, 0, byte * 50)

    ts = [threading.Thread(target=go, args=(a.id, b"a")), threading.Thread(target=go, args=(b.id, b"b"))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert m.finish(a.id).read_bytes() == b"a" * 50
    assert m.finish(b.id).read_bytes() == b"b" * 50
