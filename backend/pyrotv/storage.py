from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import Config


@dataclass(frozen=True)
class StorageInfo:
    used_bytes: int
    budget_bytes: int
    free_bytes: int
    total_bytes: int


def _tree_size(root: Path) -> int:
    """Total size of regular files under root; symlinks are never followed."""
    total = 0
    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            it = os.scandir(current)
        except OSError:
            continue
        with it:
            for entry in it:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    return total


def _existing_parent(path: Path) -> Path:
    p = path
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


def storage_usage(cfg: Config, budget_bytes: int = 40 * 1024**3) -> StorageInfo:
    used = sum(
        _tree_size(d)
        for d in (cfg.media_videos, cfg.media_images, cfg.custom_videos, cfg.custom_images)
    )
    du = shutil.disk_usage(_existing_parent(cfg.data_dir))
    return StorageInfo(
        used_bytes=used,
        budget_bytes=budget_bytes,
        free_bytes=du.free,
        total_bytes=du.total,
    )
