from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, AsyncIterator, Callable, Literal
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints

from .catalogue import Catalogue, CatalogueError, load_catalogue
from .config import Config
from .customs import CustomService, DuplicateName, UnknownManufacturer, cleanup_custom_orphans
from .events import EventBus
from .backup import BACKUP_NAME, export_backup, find_usb_mount, import_backup
from .hotspot import (
    DEFAULT_AUTOOFF_MINUTES,
    HOTSPOT_HOSTNAME,
    HOTSPOT_IP,
    Hotspot,
    HotspotError,
    run_nmcli,
    url_qr_svg,
)
from .library import Library
from .network import is_allowed_client
from .outputs import OutputWatcher
from .playback import PlaybackService
from .player import FakePlayer, MpvPlayer, Player
from .storage import storage_usage
from .store import Store
from .thumbs import ensure_thumb
from .uploads import DiskFull, IncompleteUpload, OffsetMismatch, UploadManager

log = logging.getLogger(__name__)

VIEWS_FLUSH_SECONDS = 120.0
MAX_CHUNK_BYTES = 16 * 1024 * 1024


class ClientGuard:
    """Pure ASGI middleware: only allow-listed client addresses get an answer."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            host = client[0] if client else None
            if not is_allowed_client(host):
                if scope["type"] == "http":
                    await JSONResponse({"detail": "forbidden"}, status_code=403)(
                        scope, receive, send
                    )
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
            if scope["type"] == "http" and _is_hotspot_client(host):
                # Phone activity postpones the hotspot auto-off. touch() is
                # lock-free: never take Hotspot._lock on the event loop.
                app_state = getattr(scope.get("app"), "state", None)
                hotspot = getattr(app_state, "hotspot", None)
                if hotspot is not None:
                    hotspot.touch()
        await self.app(scope, receive, send)


_HOTSPOT_NET = ipaddress.ip_network("10.42.0.0/24")
_HOTSPOT_GATEWAY = ipaddress.ip_address("10.42.0.1")


def _is_hotspot_client(host: str | None) -> bool:
    try:
        addr = ipaddress.ip_address(host or "")
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.version == 4 and addr in _HOTSPOT_NET and addr != _HOTSPOT_GATEWAY


async def sse_stream(
    bus: EventBus, outputs: OutputWatcher, keepalive: float = 15.0
) -> AsyncIterator[str]:
    q = bus.subscribe()
    try:
        first = {"type": "tv", "connected": bool(outputs.connected)}
        yield f"data: {json.dumps(first, separators=(',', ':'))}\n\n"
        while True:
            try:
                event = await asyncio.wait_for(q.get(), keepalive)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
    finally:
        bus.unsubscribe(q)


async def _views_flush_loop(store: Store) -> None:
    while True:
        await asyncio.sleep(VIEWS_FLUSH_SECONDS)
        try:
            await asyncio.to_thread(store.flush_views)
        except Exception:
            log.exception("views flush failed")


# request bodies


class SettingsBody(BaseModel):
    shop_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)] | None = None
    enabled_manufacturers: list[str] | None = None
    hotspot_autooff_minutes: int | None = Field(default=None, ge=0, le=1440)


class EnabledBody(BaseModel):
    enabled: bool


class PlayBody(BaseModel):
    key: str


class SeekBody(BaseModel):
    delta: float


class UploadBody(BaseModel):
    kind: Literal["video", "image"]
    filename: str = Field(min_length=1, max_length=255)
    size: int


class CustomBody(BaseModel):
    manufacturer_id: str | None = None
    new_manufacturer_name: str | None = Field(default=None, max_length=200)
    name: str = Field(max_length=200)
    category: str = Field(max_length=200)
    video_upload_id: str = Field(max_length=64)
    image_upload_id: str | None = Field(default=None, max_length=64)


class CustomPatchBody(BaseModel):
    name: str = Field(max_length=200)
    category: str = Field(max_length=200)


# helpers


def _state_json(request: Request) -> dict:
    s = request.app.state
    return {
        "shop_name": s.store.get_setting("shop_name", ""),
        "setup_done": s.store.get_setting("setup_done") == "1",
        "tv_connected": s.outputs.connected,
    }


def _nfc_key(key: str) -> str:
    """Piece keys are stored NFC; clients may send other Unicode forms."""
    return unicodedata.normalize("NFC", key)


def _piece_or_404(request: Request, key: str):
    piece = request.app.state.library.get(_nfc_key(key))
    if piece is None:
        raise HTTPException(404, "unknown piece")
    return piece


# routers, one per concern


def _state_router() -> APIRouter:
    r = APIRouter()

    @r.get("/api/state")
    def get_state(request: Request) -> dict:
        return _state_json(request)

    @r.put("/api/settings")
    def put_settings(body: SettingsBody, request: Request) -> dict:
        s = request.app.state
        if body.enabled_manufacturers is not None:
            known = {m.id for m in s.library.manufacturers()}
            unknown = sorted(set(body.enabled_manufacturers) - known)
            if unknown:
                raise HTTPException(422, f"unknown manufacturer id(s): {', '.join(unknown)}")
        if body.shop_name is not None:
            s.store.set_setting("shop_name", body.shop_name)
        if body.enabled_manufacturers is not None:
            s.store.set_enabled_manufacturers(body.enabled_manufacturers)
        if body.hotspot_autooff_minutes is not None:
            s.store.set_setting("hotspot_autooff_minutes", str(body.hotspot_autooff_minutes))
        return _state_json(request)

    @r.post("/api/setup/complete")
    def setup_complete(request: Request) -> dict:
        request.app.state.store.set_setting("setup_done", "1")
        return _state_json(request)

    @r.post("/api/setup/reset")
    def setup_reset(request: Request) -> dict:
        request.app.state.store.set_setting("setup_done", "0")
        return _state_json(request)

    return r


def _library_router() -> APIRouter:
    r = APIRouter()

    @r.get("/api/pieces")
    def list_pieces(
        request: Request,
        sort: Literal["name", "views"] = "name",
        category: str | None = None,
        q: str | None = None,
    ) -> list[dict]:
        views = request.app.state.library.list(sort=sort, category=category, query=q)
        return [asdict(v) for v in views]

    @r.get("/api/categories")
    def list_categories(request: Request) -> list[str]:
        return request.app.state.library.categories()

    @r.get("/api/manufacturers")
    def list_manufacturers(request: Request) -> list[dict]:
        s = request.app.state
        enabled = s.store.enabled_manufacturers()
        disabled = s.store.disabled_pieces()
        by_man: dict[str, list[dict]] = {}
        for v in s.library.list(include_disabled=True):
            by_man.setdefault(v.manufacturer_id, []).append(
                {
                    "key": v.key,
                    "name": v.name,
                    "category": v.category,
                    "enabled": v.key not in disabled,
                }
            )
        return [
            {
                "id": m.id,
                "name": m.name,
                "custom": m.id.startswith("c-"),
                "enabled": m.id in enabled,
                "categories": list(m.categories),
                "pieces": by_man.get(m.id, []),
            }
            for m in s.library.manufacturers()
        ]

    @r.put("/api/pieces/{key:path}/enabled")
    def set_enabled(key: str, body: EnabledBody, request: Request) -> dict:
        key = _piece_or_404(request, key).key
        request.app.state.store.set_piece_enabled(key, body.enabled)
        return {"key": key, "enabled": body.enabled}

    @r.post("/api/pieces/{key:path}/view", status_code=204)
    def add_view(key: str, request: Request) -> Response:
        key = _piece_or_404(request, key).key
        request.app.state.store.add_view(key)
        return Response(status_code=204)

    return r


def _media_router() -> APIRouter:
    r = APIRouter()

    @r.get("/api/thumb/{key:path}")
    def thumb(key: str, request: Request) -> FileResponse:
        s = request.app.state
        piece = _piece_or_404(request, key)
        if piece.image is None:
            raise HTTPException(404, "no image")
        try:
            path = ensure_thumb(piece.image, s.cfg.thumbs_dir)
        except Exception:
            log.exception("thumbnail failed for %s", key)
            raise HTTPException(404, "thumbnail unavailable") from None
        return FileResponse(
            path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"}
        )

    @r.get("/media/video/{key:path}")
    def video(key: str, request: Request) -> FileResponse:
        piece = _piece_or_404(request, key)
        if piece.video is None or not piece.video.is_file():
            raise HTTPException(404, "no video")
        return FileResponse(piece.video, media_type="video/mp4")

    return r


def _playback_router() -> APIRouter:
    r = APIRouter()

    @r.post("/api/play")
    async def play(body: PlayBody, request: Request) -> dict:
        try:
            start = await request.app.state.playback.start(_nfc_key(body.key))
        except KeyError:
            raise HTTPException(404, "unknown piece") from None
        url = "/media/video/" + quote(start.key, safe="") if start.mode == "local" else None
        return {"mode": start.mode, "title": start.title, "key": start.key, "video_url": url}

    @r.post("/api/player/seek", status_code=204)
    async def seek(body: SeekBody, request: Request) -> Response:
        await request.app.state.playback.command("seek", body.delta)
        return Response(status_code=204)

    @r.post("/api/player/pause", status_code=204)
    async def pause(request: Request) -> Response:
        await request.app.state.playback.command("pause")
        return Response(status_code=204)

    @r.post("/api/player/stop", status_code=204)
    async def stop(request: Request) -> Response:
        await request.app.state.playback.command("stop")
        return Response(status_code=204)

    @r.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        s = request.app.state
        return StreamingResponse(
            sse_stream(s.bus, s.outputs),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return r


def _uploads_router() -> APIRouter:
    r = APIRouter()

    @r.post("/api/uploads")
    def create_upload(body: UploadBody, request: Request) -> dict:
        try:
            info = request.app.state.uploads.create(body.kind, body.filename, body.size)
        except DiskFull:
            raise HTTPException(507, "not enough free disk space") from None
        except ValueError as e:
            raise HTTPException(413 if "size" in str(e) else 415, str(e)) from None
        return asdict(info)

    @r.get("/api/uploads/{upload_id}")
    def get_upload(upload_id: str, request: Request) -> dict:
        try:
            return asdict(request.app.state.uploads.status(upload_id))
        except KeyError:
            raise HTTPException(404, "unknown upload") from None

    @r.put("/api/uploads/{upload_id}")
    async def put_chunk(upload_id: str, offset: int, request: Request):
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_CHUNK_BYTES:
            raise HTTPException(413, "chunk too large")
        buf = bytearray()
        async for part in request.stream():
            buf += part
            if len(buf) > MAX_CHUNK_BYTES:
                raise HTTPException(413, "chunk too large")
        try:
            received = await asyncio.to_thread(
                request.app.state.uploads.write_chunk, upload_id, offset, bytes(buf)
            )
        except KeyError:
            raise HTTPException(404, "unknown upload") from None
        except OffsetMismatch as e:
            return JSONResponse({"received": e.received}, status_code=409)
        except DiskFull:
            raise HTTPException(507, "not enough free disk space") from None
        except ValueError as e:
            raise HTTPException(413, str(e)) from None
        return {"received": received}

    @r.post("/api/uploads/{upload_id}/finish")
    def finish_upload(upload_id: str, request: Request):
        uploads = request.app.state.uploads
        try:
            uploads.finish(upload_id)
            info = uploads.status(upload_id)
        except KeyError:
            raise HTTPException(404, "unknown upload") from None
        except IncompleteUpload as e:
            return JSONResponse({"received": e.received, "size": e.size}, status_code=409)
        return {"received": info.received, "size": info.size}

    return r


def _custom_router() -> APIRouter:
    r = APIRouter()

    @r.post("/api/custom", status_code=202)
    def create_custom(body: CustomBody, request: Request) -> dict:
        try:
            job_id = request.app.state.customs.create(
                body.manufacturer_id,
                body.new_manufacturer_name,
                body.name,
                body.category,
                body.video_upload_id,
                body.image_upload_id,
            )
        except DuplicateName:
            raise HTTPException(409, "a piece with that name already exists") from None
        except UnknownManufacturer:
            raise HTTPException(404, "unknown manufacturer") from None
        except KeyError:
            raise HTTPException(404, "unknown upload") from None
        except IncompleteUpload:
            raise HTTPException(409, "upload is incomplete") from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return {"job_id": job_id}

    @r.get("/api/custom/jobs/{job_id}")
    def get_job(job_id: str, request: Request) -> dict:
        try:
            return request.app.state.customs.job(job_id)
        except KeyError:
            raise HTTPException(404, "unknown job") from None

    @r.get("/api/custom")
    def list_custom(request: Request) -> list[dict]:
        return request.app.state.customs.list()

    @r.patch("/api/custom/{piece_id}")
    def patch_custom(piece_id: int, body: CustomPatchBody, request: Request) -> dict:
        try:
            request.app.state.customs.update(piece_id, body.name, body.category)
        except KeyError:
            raise HTTPException(404, "unknown custom piece") from None
        except DuplicateName:
            raise HTTPException(409, "a piece with that name already exists") from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return {"id": piece_id}

    @r.delete("/api/custom/{piece_id}", status_code=204)
    def delete_custom(piece_id: int, request: Request) -> Response:
        request.app.state.customs.delete(piece_id)
        return Response(status_code=204)

    return r


HOTSPOT_URL = f"http://{HOTSPOT_HOSTNAME}"
HOTSPOT_IP_URL = f"http://{HOTSPOT_IP}"


class HotspotBody(BaseModel):
    on: bool


def _hotspot_json(h: Hotspot, store: Store) -> dict:
    pin = h.pin
    try:
        autooff = int(store.get_setting("hotspot_autooff_minutes", str(DEFAULT_AUTOOFF_MINUTES)))
    except (TypeError, ValueError):
        autooff = DEFAULT_AUTOOFF_MINUTES
    return {
        "on": pin is not None and h.is_up,
        "ssid": h.ssid,
        "pin": pin,
        "url": HOTSPOT_URL,
        "ip_url": HOTSPOT_IP_URL,
        "qr_svg": h.qr_svg(),  # joins the Wi-Fi
        "url_qr_svg": url_qr_svg(HOTSPOT_IP_URL) if pin is not None else None,  # opens the page
        "autooff_minutes": autooff,
    }


def _ops_router() -> APIRouter:
    r = APIRouter()

    @r.get("/api/storage")
    def get_storage(request: Request) -> dict:
        return asdict(storage_usage(request.app.state.cfg))

    @r.get("/api/hotspot")
    def get_hotspot(request: Request, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        return _hotspot_json(request.app.state.hotspot, request.app.state.store)

    @r.post("/api/hotspot")
    def set_hotspot(body: HotspotBody, request: Request, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        h = request.app.state.hotspot
        if body.on:
            try:
                h.up()
            except HotspotError:
                log.exception("hotspot start failed")
                raise HTTPException(502, "hotspot_failed") from None
        else:
            h.down()
        return _hotspot_json(h, request.app.state.store)

    def _usb(request: Request) -> Path:
        s = request.app.state
        mount = find_usb_mount(s.usb_root, s.usb_is_mount)
        if mount is None:
            raise HTTPException(409, "no_usb")
        return mount

    @r.post("/api/backup/export")
    def backup_export(request: Request) -> dict:
        mount = _usb(request)
        try:
            path = export_backup(request.app.state.store, mount)
        except OSError:
            log.exception("backup export failed")
            raise HTTPException(409, "no_usb") from None
        return {"path": str(path)}

    @r.post("/api/backup/import")
    def backup_import(request: Request) -> dict:
        mount = _usb(request)
        path = mount / BACKUP_NAME
        if not path.is_file():
            raise HTTPException(404, "no_backup")
        try:
            import_backup(request.app.state.store, path)
        except (ValueError, OSError):
            raise HTTPException(422, "invalid_backup") from None
        return {"imported": True}

    @r.get("/api/diagnostics")
    def diagnostics(request: Request) -> dict:
        s = request.app.state
        return {
            "missing_media": list(s.catalogue.missing),
            "free_bytes": storage_usage(s.cfg).free_bytes,
            "catalogue_error": s.catalogue_error,
        }

    return r


def create_app(
    cfg: Config,
    *,
    player: Player | None = None,
    watcher_interval: float = 2.0,
    custom_process: tuple[Callable[[Path, Path], None], Callable[[Path, Path], None]] | None = None,
    hotspot_run: Callable[[list[str]], int] | None = None,
) -> FastAPI:
    """`custom_process=(process_video, process_image)` replaces the ffmpeg/Pillow
    pipeline of CustomService; `hotspot_run` replaces the nmcli runner (test seams;
    production leaves both None)."""
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg.ensure_dirs()
        store = Store(cfg.db_path)
        tasks: list[asyncio.Task] = []
        customs: CustomService | None = None
        try:
            catalogue_error: str | None = None
            try:
                catalogue = load_catalogue(cfg.catalogue_path, cfg.media_videos, cfg.media_images)
            except CatalogueError as e:
                log.error("catalogue unavailable: %s", e)
                catalogue_error = str(e)
                catalogue = Catalogue([], [], [])
            removed = cleanup_custom_orphans(cfg, store)
            if removed:
                log.info("boot cleanup removed %d stale file(s)", removed)
            library = Library(cfg, catalogue, store)
            uploads = UploadManager(cfg)
            proc = {}
            if custom_process is not None:
                proc = {"process_video": custom_process[0], "process_image": custom_process[1]}
            customs = CustomService(cfg, store, library, uploads, **proc)
            bus = EventBus()
            outputs = OutputWatcher(cfg, bus, watcher_interval)
            pl = player or (FakePlayer() if cfg.fake_hw else MpvPlayer(cfg))
            playback = PlaybackService(library, outputs, pl, bus, cfg)

            s = app.state
            s.cfg, s.store, s.catalogue, s.catalogue_error = cfg, store, catalogue, catalogue_error
            s.library, s.bus, s.outputs, s.player, s.playback = library, bus, outputs, pl, playback
            s.uploads, s.customs = uploads, customs
            runner = hotspot_run or ((lambda args: 0) if cfg.fake_hw else run_nmcli)
            hotspot = Hotspot(cfg, store, runner)
            s.hotspot = hotspot
            s.usb_root, s.usb_is_mount = Path("/media"), os.path.ismount
            # The hotspot must be off after boot (the PIN is lost on restart anyway).
            await asyncio.to_thread(hotspot.down)

            tasks = [
                asyncio.create_task(hotspot.run_autooff(), name="hotspot-autooff"),
                asyncio.create_task(outputs.run(), name="outputs"),
                asyncio.create_task(playback.run(), name="playback"),
                asyncio.create_task(_views_flush_loop(store), name="views-flush"),
            ]
            yield
        finally:
            for t in tasks:
                t.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for t, res in zip(tasks, results):
                if isinstance(res, Exception) and not isinstance(res, asyncio.CancelledError):
                    log.error("background task %s failed: %r", t.get_name(), res)
            try:
                if customs is not None:
                    await asyncio.to_thread(customs.close)
                store.flush_views()
            finally:
                store.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(ClientGuard)
    app.include_router(_state_router())
    app.include_router(_library_router())
    app.include_router(_media_router())
    app.include_router(_playback_router())
    app.include_router(_uploads_router())
    app.include_router(_custom_router())
    app.include_router(_ops_router())
    # Later tasks: include their routers here, before the static mount.
    if cfg.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=cfg.static_dir, html=True), name="static")
    return app
