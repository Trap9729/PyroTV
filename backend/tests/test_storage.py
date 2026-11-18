from __future__ import annotations

from pathlib import Path

from pyrotv.config import Config
from pyrotv.storage import StorageInfo, storage_usage


def test_usage_sums_known_files(tmp_path: Path):
    cfg = Config(data_dir=tmp_path / "d", catalogue_path=tmp_path / "c.json", static_dir=tmp_path)
    cfg.ensure_dirs()
    (cfg.media_videos / "a.mp4").write_bytes(b"x" * 100)
    (cfg.media_images / "a.jpg").write_bytes(b"x" * 20)
    (cfg.custom_videos / "sub").mkdir()
    (cfg.custom_videos / "sub" / "b.mp4").write_bytes(b"x" * 300)
    (cfg.custom_images / "b.jpg").write_bytes(b"x" * 4)
    (cfg.thumbs_dir / "t.jpg").write_bytes(b"x" * 9999)  # not counted
    info = storage_usage(cfg, budget_bytes=1000)
    assert isinstance(info, StorageInfo)
    assert info.used_bytes == 424
    assert info.budget_bytes == 1000
    assert info.free_bytes > 0
    assert info.total_bytes >= info.free_bytes


def test_usage_missing_dirs_and_data_dir(tmp_path: Path):
    cfg = Config(
        data_dir=tmp_path / "nope" / "deeper", catalogue_path=tmp_path / "c", static_dir=tmp_path
    )
    info = storage_usage(cfg)
    assert info.used_bytes == 0
    assert info.budget_bytes == 40 * 1024**3
    assert info.free_bytes > 0
