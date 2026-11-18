from __future__ import annotations

import logging
import queue
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import media
from .catalogue import make_key
from .config import Config
from .library import Library
from .store import Store, _slug
from .uploads import UploadManager

log = logging.getLogger(__name__)

MAX_NAME = 80
MAX_CATEGORY = 40
MAX_MANUFACTURER = 40
MAX_JOBS_KEPT = 200


class DuplicateName(Exception):
    pass


class UnknownManufacturer(Exception):
    pass


def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", s).strip()


def _fold(s: str) -> str:
    return _norm(s).casefold()


def _bounded(value: str | None, label: str, maxlen: int) -> str:
    v = _norm(value or "")
    if not 1 <= len(v) <= maxlen:
        raise ValueError(f"{label} must be 1..{maxlen} characters")
    return v


@dataclass(frozen=True)
class _Job:
    id: str
    manufacturer_id: str | None
    new_manufacturer_name: str | None
    name: str
    category: str
    video_upload_id: str
    video_src: Path
    image_upload_id: str | None
    image_src: Path | None


class CustomService:
    """Custom pieces: validation is synchronous, media processing runs on one worker thread.

    `process_video` / `process_image` are injectable so tests can stub ffmpeg.
    """

    def __init__(
        self,
        cfg: Config,
        store: Store,
        library: Library,
        uploads: UploadManager,
        *,
        process_video: Callable[[Path, Path], None] = media.process_video,
        process_image: Callable[[Path, Path], None] = media.process_image,
    ) -> None:
        self._cfg = cfg
        self._store = store
        self._library = library
        self._uploads = uploads
        self._process_video = process_video
        self._process_image = process_image
        self._jobs: dict[str, dict] = {}
        self._jobs_lock = threading.Lock()
        self._commit_lock = threading.RLock()  # serialises writes that touch names
        self._queue: queue.Queue[_Job | None] = queue.Queue()
        self._worker = threading.Thread(target=self._run, name="custom-worker", daemon=True)
        self._worker.start()

    # lookups
    def _manufacturer_name_index(self) -> dict[str, str]:
        return {_fold(m.name): m.id for m in self._library.manufacturers()}

    def _check_duplicate(self, manufacturer_id: str, name: str, exclude_id: int | None = None) -> None:
        folded = _fold(name)
        own_key = None
        rows = self._store.list_custom_pieces()
        for r in rows:
            if r.id == exclude_id:
                own_key = make_key(r.manufacturer_id, r.name)
            elif r.manufacturer_id == manufacturer_id and _fold(r.name) == folded:
                raise DuplicateName(name)
        for v in self._library.list(include_disabled=True):
            if v.manufacturer_id == manufacturer_id and v.key != own_key and _fold(v.name) == folded:
                raise DuplicateName(name)

    # api
    def create(
        self,
        manufacturer_id: str | None,
        new_manufacturer_name: str | None,
        name: str,
        category: str,
        video_upload_id: str,
        image_upload_id: str | None,
    ) -> str:
        if (manufacturer_id is None) == (new_manufacturer_name is None):
            raise ValueError("give exactly one of manufacturer_id and new_manufacturer_name")
        name = _bounded(name, "name", MAX_NAME)
        category = _bounded(category, "category", MAX_CATEGORY)
        new_name: str | None = None
        resolved: str | None = None
        if manufacturer_id is not None:
            if manufacturer_id not in {m.id for m in self._library.manufacturers()}:
                raise UnknownManufacturer(manufacturer_id)
            resolved = manufacturer_id
        else:
            new_name = _bounded(new_manufacturer_name, "manufacturer name", MAX_MANUFACTURER)
            if not _slug(new_name):
                raise ValueError("manufacturer name has no usable characters")
            resolved = self._manufacturer_name_index().get(_fold(new_name))
        if resolved is not None:
            self._check_duplicate(resolved, name)

        vinfo = self._uploads.status(video_upload_id)
        if vinfo.kind != "video":
            raise ValueError("video_upload_id is not a video upload")
        video_src = self._uploads.finish(video_upload_id)
        image_src = None
        if image_upload_id is not None:
            iinfo = self._uploads.status(image_upload_id)
            if iinfo.kind != "image":
                raise ValueError("image_upload_id is not an image upload")
            image_src = self._uploads.finish(image_upload_id)

        job = _Job(
            uuid.uuid4().hex, resolved, new_name, name, category,
            video_upload_id, video_src, image_upload_id, image_src,
        )
        with self._jobs_lock:
            self._jobs[job.id] = {"state": "processing", "message": None}
            if len(self._jobs) > MAX_JOBS_KEPT:
                for jid in [k for k, v in self._jobs.items() if v["state"] != "processing"][
                    : len(self._jobs) - MAX_JOBS_KEPT
                ]:
                    del self._jobs[jid]
        self._queue.put(job)
        return job.id

    def job(self, job_id: str) -> dict:
        with self._jobs_lock:
            return dict(self._jobs[job_id])

    def list(self) -> list[dict]:
        names = {m.id: m.name for m in self._library.manufacturers()}
        out = []
        for r in self._store.list_custom_pieces():
            has_image = bool(r.image_file) and (self._cfg.custom_images / r.image_file).is_file()
            out.append(
                {
                    "id": r.id,
                    "manufacturer_id": r.manufacturer_id,
                    "manufacturer_name": names.get(r.manufacturer_id, r.manufacturer_id),
                    "name": r.name,
                    "category": r.category,
                    "key": make_key(r.manufacturer_id, r.name),
                    "has_image": has_image,
                }
            )
        return out

    def update(self, id: int, name: str, category: str) -> None:
        name = _bounded(name, "name", MAX_NAME)
        category = _bounded(category, "category", MAX_CATEGORY)
        with self._commit_lock:
            row = next((r for r in self._store.list_custom_pieces() if r.id == id), None)
            if row is None:
                raise KeyError(id)
            self._check_duplicate(row.manufacturer_id, name, exclude_id=id)
            self._store.update_custom_piece(id, name, category)
            self._library.reload_custom()

    def delete(self, id: int) -> None:
        with self._commit_lock:
            row = self._store.delete_custom_piece(id)
            if row is None:
                return
            self._library.reload_custom()
        (self._cfg.custom_videos / row.video_file).unlink(missing_ok=True)
        if row.image_file:
            (self._cfg.custom_images / row.image_file).unlink(missing_ok=True)

    def close(self) -> None:
        self._queue.put(None)
        self._worker.join(timeout=5)

    # worker
    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                self._process(job)
                state = {"state": "done", "message": None}
            except Exception as e:  # noqa: BLE001 - reported to the client
                log.exception("custom piece job %s failed", job.id)
                state = {"state": "error", "message": (str(e) or type(e).__name__)[:200]}
            with self._jobs_lock:
                self._jobs[job.id] = state

    def _process(self, job: _Job) -> None:
        cfg = self._cfg
        video_name = f"{uuid.uuid4().hex}.mp4"
        image_name = f"{uuid.uuid4().hex}.jpg" if job.image_src else None
        video_dest = cfg.custom_videos / video_name
        image_dest = cfg.custom_images / image_name if image_name else None
        created = [video_dest, video_dest.with_name(video_name + ".part")]
        if image_dest is not None:
            created += [image_dest, image_dest.with_name(image_name + ".part")]
        try:
            self._process_video(job.video_src, video_dest)
            if image_dest is not None:
                self._process_image(job.image_src, image_dest)
            with self._commit_lock:
                mid = job.manufacturer_id
                if mid is None:
                    mid = self._manufacturer_name_index().get(_fold(job.new_manufacturer_name or ""))
                if mid is not None:
                    self._check_duplicate(mid, job.name)
                if mid is None:
                    mid = self._store.add_custom_manufacturer(job.new_manufacturer_name or "")
                self._store.set_enabled_manufacturers(self._store.enabled_manufacturers() | {mid})
                self._store.add_custom_piece(mid, job.name, job.category, video_name, image_name)
                self._library.reload_custom()
        except BaseException:
            for p in created:
                p.unlink(missing_ok=True)
            raise
        finally:
            self._uploads.discard(job.video_upload_id)
            if job.image_upload_id:
                self._uploads.discard(job.image_upload_id)


def cleanup_custom_orphans(cfg: Config, store: Store) -> int:
    """Boot cleanup: stale upload temp files, *.part leftovers and unreferenced custom files."""
    removed = UploadManager(cfg).cleanup_stale()
    rows = store.list_custom_pieces()
    keep = {
        cfg.custom_videos: {r.video_file for r in rows},
        cfg.custom_images: {r.image_file for r in rows if r.image_file},
    }
    for directory, referenced in keep.items():
        if not directory.is_dir():
            continue
        for p in directory.iterdir():
            if p.is_file() and (p.name.endswith(".part") or p.name not in referenced):
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    log.warning("could not remove %s", p)
    return removed
