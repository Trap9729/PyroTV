from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

from PIL import Image

_lock = threading.Lock()


def _cache_path(src: Path, cache_dir: Path, width: int) -> Path:
    digest = hashlib.sha1(f"{src}|{src.stat().st_mtime_ns}|{width}".encode("utf-8"))
    return cache_dir / (digest.hexdigest()[:16] + ".jpg")


def _usable(out: Path) -> bool:
    """A 0-byte cache file (power cut before the data reached the disk) is a miss."""
    try:
        return out.stat().st_size > 0
    except OSError:
        return False


def ensure_thumb(src: Path, cache_dir: Path, width: int = 480) -> Path:
    """Return a cached JPEG thumbnail of `src` (at most `width` px wide, never upscaled)."""
    src = Path(src)
    cache_dir = Path(cache_dir)
    out = _cache_path(src, cache_dir, width)
    if _usable(out):
        return out
    with _lock:
        if _usable(out):
            return out
        out.unlink(missing_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        try:
            with Image.open(src) as img:
                if img.format == "JPEG":
                    img.draft("RGB", (width * 2, width * 2))
                rgb = img.convert("RGB")
            try:
                rgb.thumbnail((width, width * 10))
                with open(tmp, "wb") as f:
                    rgb.save(f, format="JPEG", quality=80)
                    f.flush()
                    os.fsync(f.fileno())
            finally:
                rgb.close()
            os.replace(tmp, out)
        finally:
            tmp.unlink(missing_ok=True)
    return out
