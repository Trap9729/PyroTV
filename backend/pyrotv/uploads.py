from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from .config import Config

GIB = 1024**3
MIB = 1024**2
MAX_VIDEO = 4 * GIB
MAX_IMAGE = 50 * MIB
DISK_RESERVE = 1 * GIB

VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")

_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class DiskFull(Exception):
    pass


class OffsetMismatch(Exception):
    def __init__(self, received: int) -> None:
        super().__init__(f"offset mismatch, received={received}")
        self.received = received


class IncompleteUpload(Exception):
    def __init__(self, received: int, size: int) -> None:
        super().__init__(f"incomplete upload: {received} of {size} bytes")
        self.received = received
        self.size = size


@dataclass(frozen=True)
class UploadInfo:
    id: str
    kind: Literal["video", "image"]
    filename: str
    size: int
    received: int


def _default_free(path: Path) -> int:
    return shutil.disk_usage(path).free


class UploadManager:
    """Resumable uploads. State lives on disk (<id>.part + <id>.json), so it survives restarts."""

    def __init__(self, cfg: Config, free_bytes: Callable[[Path], int] = _default_free) -> None:
        self._cfg = cfg
        self._free = free_bytes
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, upload_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(upload_id, threading.Lock())

    # paths
    def _check(self, upload_id: str) -> str:
        if not isinstance(upload_id, str) or not _ID_RE.match(upload_id):
            raise KeyError(upload_id)
        return upload_id

    def _part(self, upload_id: str) -> Path:
        return self._cfg.upload_tmp_dir / f"{upload_id}.part"

    def _meta(self, upload_id: str) -> Path:
        return self._cfg.upload_tmp_dir / f"{upload_id}.json"

    def _load(self, upload_id: str) -> tuple[dict, Path]:
        self._check(upload_id)
        try:
            meta = json.loads(self._meta(upload_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise KeyError(upload_id) from None
        part = self._part(upload_id)
        if not part.is_file():
            raise KeyError(upload_id)
        return meta, part

    # api
    def create(self, kind: str, filename: str, size: int) -> UploadInfo:
        if kind not in ("video", "image"):
            raise ValueError("unsupported kind")
        ext = Path(filename).suffix.lower()
        if ext not in (VIDEO_EXTS if kind == "video" else IMAGE_EXTS):
            raise ValueError(f"unsupported file type {ext!r} for {kind}")
        limit = MAX_VIDEO if kind == "video" else MAX_IMAGE
        if not isinstance(size, int) or size <= 0 or size > limit:
            raise ValueError(f"invalid size {size} (max {limit} bytes for {kind})")
        tmp = self._cfg.upload_tmp_dir
        tmp.mkdir(parents=True, exist_ok=True)
        if self._free(self._cfg.data_dir) < size + DISK_RESERVE:
            raise DiskFull("not enough free disk space")
        uid = uuid.uuid4().hex
        self._meta(uid).write_text(
            json.dumps({"kind": kind, "filename": filename, "size": size}), encoding="utf-8"
        )
        self._part(uid).touch()
        return UploadInfo(uid, kind, filename, size, 0)  # type: ignore[arg-type]

    def status(self, upload_id: str) -> UploadInfo:
        meta, part = self._load(upload_id)
        return UploadInfo(
            upload_id, meta["kind"], meta["filename"], meta["size"], part.stat().st_size
        )

    def write_chunk(self, upload_id: str, offset: int, data: bytes) -> int:
        self._check(upload_id)
        with self._lock_for(upload_id):
            return self._write_chunk_locked(upload_id, offset, data)

    def _write_chunk_locked(self, upload_id: str, offset: int, data: bytes) -> int:
        meta, part = self._load(upload_id)
        received = part.stat().st_size
        if offset != received:
            raise OffsetMismatch(received)
        if received + len(data) > meta["size"]:
            raise ValueError("chunk exceeds declared upload size")
        with open(part, "ab") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        return received + len(data)

    def finish(self, upload_id: str) -> Path:
        self._check(upload_id)
        with self._lock_for(upload_id):
            return self._finish_locked(upload_id)

    def _finish_locked(self, upload_id: str) -> Path:
        meta, part = self._load(upload_id)
        received = part.stat().st_size
        if received != meta["size"]:
            raise IncompleteUpload(received, meta["size"])
        with open(part, "rb+") as f:
            os.fsync(f.fileno())
        return part

    def discard(self, upload_id: str) -> None:
        try:
            self._check(upload_id)
        except KeyError:
            return
        with self._lock_for(upload_id):
            for p in (self._part(upload_id), self._meta(upload_id)):
                p.unlink(missing_ok=True)
        with self._locks_guard:
            self._locks.pop(upload_id, None)

    def cleanup_stale(self) -> int:
        count = 0
        with self._locks_guard:
            self._locks.clear()
        d = self._cfg.upload_tmp_dir
        if not d.is_dir():
            return 0
        for p in d.iterdir():
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                count += 1
            except OSError:
                pass
        return count
