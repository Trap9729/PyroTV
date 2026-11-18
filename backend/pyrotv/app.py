from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import AsyncIterator, Literal
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated

from .catalogue import Catalogue, CatalogueError, load_catalogue
from .config import Config
from .events import EventBus
from .library import Library
from .network import is_allowed_client
from .outputs import OutputWatcher
from .playback import PlaybackService
from .player import FakePlayer, MpvPlayer, Player
from .store import Store
from .thumbs import ensure_thumb

log = logging.getLogger(__name__)

VIEWS_FLUSH_SECONDS = 120.0


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
        await self.app(scope, receive, send)


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


# helpers


def _state_json(request: Request) -> dict:
    s = request.app.state
    return {
        "shop_name": s.store.get_setting("shop_name", ""),
        "setup_done": s.store.get_setting("setup_done") == "1",
        "tv_connected": s.outputs.connected,
    }


def _piece_or_404(request: Request, key: str):
    piece = request.app.state.library.get(key)
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
                {"key": v.key, "name": v.name, "enabled": v.key not in disabled}
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
        _piece_or_404(request, key)
        request.app.state.store.set_piece_enabled(key, body.enabled)
        return {"key": key, "enabled": body.enabled}

    @r.post("/api/pieces/{key:path}/view", status_code=204)
    def add_view(key: str, request: Request) -> Response:
        _piece_or_404(request, key)
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
            start = await request.app.state.playback.start(body.key)
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


def create_app(
    cfg: Config, *, player: Player | None = None, watcher_interval: float = 2.0
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg.ensure_dirs()
        store = Store(cfg.db_path)
        tasks: list[asyncio.Task] = []
        try:
            catalogue_error: str | None = None
            try:
                catalogue = load_catalogue(cfg.catalogue_path, cfg.media_videos, cfg.media_images)
            except CatalogueError as e:
                log.error("catalogue unavailable: %s", e)
                catalogue_error = str(e)
                catalogue = Catalogue([], [], [])
            library = Library(cfg, catalogue, store)
            bus = EventBus()
            outputs = OutputWatcher(cfg, bus, watcher_interval)
            pl = player or (FakePlayer() if cfg.fake_hw else MpvPlayer(cfg))
            playback = PlaybackService(library, outputs, pl, bus, cfg)

            s = app.state
            s.cfg, s.store, s.catalogue, s.catalogue_error = cfg, store, catalogue, catalogue_error
            s.library, s.bus, s.outputs, s.player, s.playback = library, bus, outputs, pl, playback

            tasks = [
                asyncio.create_task(outputs.run(), name="outputs"),
                asyncio.create_task(playback.run(), name="playback"),
                asyncio.create_task(_views_flush_loop(store), name="views-flush"),
            ]
            yield
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await t
            try:
                store.flush_views()
            finally:
                store.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(ClientGuard)
    app.include_router(_state_router())
    app.include_router(_library_router())
    app.include_router(_media_router())
    app.include_router(_playback_router())
    # Later tasks: include their routers here, before the static mount.
    if cfg.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=cfg.static_dir, html=True), name="static")
    return app
