from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from pyrotv.thumbs import ensure_thumb


def test_large_image_is_downscaled_jpeg(tmp_path: Path):
    src = tmp_path / "a.jpg"
    Image.new("RGB", (1200, 800), (1, 2, 3)).save(src)
    out = ensure_thumb(src, tmp_path / "cache")
    with Image.open(out) as im:
        assert im.format == "JPEG"
        assert im.width <= 480


def test_second_call_hits_cache(tmp_path: Path):
    src = tmp_path / "a.jpg"
    Image.new("RGB", (1200, 800)).save(src)
    first = ensure_thumb(src, tmp_path / "cache")
    os.utime(first, ns=(1_000_000_000, 1_000_000_000))
    second = ensure_thumb(src, tmp_path / "cache")
    assert second == first
    assert second.stat().st_mtime_ns == 1_000_000_000


def test_zero_byte_cache_file_is_regenerated(tmp_path: Path):
    src = tmp_path / "a.jpg"
    Image.new("RGB", (1200, 800)).save(src)
    first = ensure_thumb(src, tmp_path / "cache")
    first.write_bytes(b"")  # power cut before the data reached the disk
    second = ensure_thumb(src, tmp_path / "cache")
    assert second == first and second.stat().st_size > 0
    with Image.open(second) as im:
        assert im.format == "JPEG"


def test_small_image_not_upscaled(tmp_path: Path):
    src = tmp_path / "s.jpg"
    Image.new("RGB", (200, 100)).save(src)
    with Image.open(ensure_thumb(src, tmp_path / "cache")) as im:
        assert im.size == (200, 100)


def test_png_with_alpha(tmp_path: Path):
    src = tmp_path / "p.png"
    Image.new("RGBA", (900, 900), (255, 0, 0, 80)).save(src)
    with Image.open(ensure_thumb(src, tmp_path / "cache")) as im:
        assert im.format == "JPEG"
        assert im.mode == "RGB"


def test_corrupt_file_raises(tmp_path: Path):
    src = tmp_path / "bad.jpg"
    src.write_bytes(b"not an image")
    with pytest.raises(Exception):
        ensure_thumb(src, tmp_path / "cache")
    assert not list((tmp_path / "cache").glob("*.tmp"))
