from __future__ import annotations

import asyncio
import io
import json
from urllib.parse import quote

from PIL import Image

from conftest import KEY_ASKA, KEY_BLIXT, KEY_ORN, OUTSIDER, PHONE, TOUCH, VIDEO_BYTES
from pyrotv.app import sse_stream
from pyrotv.events import EventBus
from pyrotv.player import FakePlayer


def enable_all(c):
    r = c.put("/api/settings", json={"enabled_manufacturers": ["lesli", "pyro"]})
    assert r.status_code == 200


def test_outsider_gets_403_everywhere(make_client, cfg):
    cfg.static_dir.mkdir()
    (cfg.static_dir / "index.html").write_text("hi")
    with make_client(OUTSIDER) as c:
        for path in ("/api/state", "/", "/media/video/" + quote(KEY_ASKA)):
            r = c.get(path)
            assert r.status_code == 403
            assert r.json() == {"detail": "forbidden"}


def test_static_mounted_last_when_present(make_client, cfg):
    cfg.static_dir.mkdir()
    (cfg.static_dir / "index.html").write_text("hello kiosk")
    with make_client(TOUCH) as c:
        assert "hello kiosk" in c.get("/").text
        assert c.get("/api/state").status_code == 200


def test_fresh_state(make_client):
    with make_client(TOUCH) as c:
        assert c.get("/api/state").json() == {
            "shop_name": "",
            "setup_done": False,
            "tv_connected": False,
        }


def test_phone_can_change_settings_and_play(make_client):
    with make_client(PHONE) as c:
        r = c.put(
            "/api/settings",
            json={"shop_name": "  Min Butik ", "enabled_manufacturers": ["lesli"]},
        )
        assert r.status_code == 200
        assert r.json()["shop_name"] == "Min Butik"
        r = c.post("/api/play", json={"key": KEY_ASKA})
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "local"
        assert body["title"] == "Åska 49"
        assert body["key"] == KEY_ASKA
        assert body["video_url"] == "/media/video/" + quote(KEY_ASKA, safe="")


def test_settings_validation(make_client):
    with make_client(TOUCH) as c:
        assert c.put("/api/settings", json={"shop_name": "   "}).status_code == 422
        assert c.put("/api/settings", json={"hotspot_autooff_minutes": 2000}).status_code == 422
        r = c.put("/api/settings", json={"enabled_manufacturers": ["nope"]})
        assert r.status_code == 422
        assert "nope" in json.dumps(r.json())
        r = c.put("/api/settings", json={"hotspot_autooff_minutes": 30})
        assert r.status_code == 200
        assert c.app_state.store.get_setting("hotspot_autooff_minutes") == "30"


def test_sort_views_and_enabled_flow(make_client):
    with make_client(TOUCH) as c:
        enable_all(c)
        names = [p["name"] for p in c.get("/api/pieces").json()]
        assert names == ["Blixt", "Åska 49", "Örn"]
        assert c.post(f"/api/pieces/{KEY_ORN}/view").status_code == 204
        assert c.post(f"/api/pieces/{KEY_ORN}/view").status_code == 204
        assert c.post(f"/api/pieces/{KEY_BLIXT}/view").status_code == 204
        by_views = c.get("/api/pieces", params={"sort": "views"}).json()
        assert [p["key"] for p in by_views][:2] == [KEY_ORN, KEY_BLIXT]
        assert by_views[0]["views"] == 2
        assert c.get("/api/pieces", params={"sort": "bogus"}).status_code == 422
        assert [p["key"] for p in c.get("/api/pieces", params={"q": "blix"}).json()] == [KEY_BLIXT]
        cat = c.get("/api/pieces", params={"category": "Åska"}).json()
        assert [p["key"] for p in cat] == [KEY_ASKA]
        assert "Åska" in c.get("/api/categories").json()

        r = c.put(f"/api/pieces/{KEY_BLIXT}/enabled", json={"enabled": False})
        assert r.status_code == 200
        assert r.json() == {"key": KEY_BLIXT, "enabled": False}
        assert KEY_BLIXT not in [p["key"] for p in c.get("/api/pieces").json()]
        mans = {m["id"]: m for m in c.get("/api/manufacturers").json()}
        lesli = mans["lesli"]
        assert lesli["custom"] is False and lesli["enabled"] is True
        assert lesli["categories"] == ["Batterier", "Åska"]
        states = {p["key"]: p["enabled"] for p in lesli["pieces"]}
        assert states == {KEY_BLIXT: False, KEY_ASKA: True}
        assert [p["name"] for p in lesli["pieces"]] == ["Blixt", "Åska 49"]


def test_unknown_key_404(make_client):
    with make_client(TOUCH) as c:
        bad = "lesli:Finns inte"
        assert c.get(f"/api/thumb/{bad}").status_code == 404
        assert c.get(f"/media/video/{bad}").status_code == 404
        assert c.put(f"/api/pieces/{bad}/enabled", json={"enabled": True}).status_code == 404
        assert c.post(f"/api/pieces/{bad}/view").status_code == 404
        assert c.post("/api/play", json={"key": bad}).status_code == 404


def test_thumb_jpeg_and_cache(make_client, cfg):
    with make_client(TOUCH) as c:
        r = c.get("/api/thumb/" + quote(KEY_ASKA))
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert "max-age" in r.headers["cache-control"]
        with Image.open(io.BytesIO(r.content)) as im:
            assert im.width <= 480
        files = list(cfg.thumbs_dir.glob("*.jpg"))
        assert len(files) == 1
        mtime = files[0].stat().st_mtime_ns
        assert c.get("/api/thumb/" + quote(KEY_ASKA)).status_code == 200
        assert files[0].stat().st_mtime_ns == mtime
        assert len(list(cfg.thumbs_dir.glob("*.jpg"))) == 1
        # piece without image
        assert c.get("/api/thumb/" + quote(KEY_BLIXT)).status_code == 404


def test_video_range(make_client):
    with make_client(TOUCH) as c:
        r = c.get("/media/video/" + quote(KEY_ASKA), headers={"Range": "bytes=0-99"})
        assert r.status_code == 206
        assert len(r.content) == 100
        assert r.content == VIDEO_BYTES[:100]
        full = c.get("/media/video/" + quote(KEY_ASKA))
        assert full.status_code == 200
        assert full.headers["content-type"] == "video/mp4"


def test_play_tv_and_seek(make_client):
    player = FakePlayer()
    with make_client(TOUCH, tv=True, player=player) as c:
        enable_all(c)
        assert c.get("/api/state").json()["tv_connected"] is True
        r = c.post("/api/play", json={"key": KEY_ASKA})
        assert r.status_code == 200
        assert r.json()["mode"] == "tv"
        assert r.json()["video_url"] is None
        assert c.post("/api/player/seek", json={"delta": 10}).status_code == 204
        assert player._position == 10
        assert c.post("/api/player/pause").status_code == 204
        assert player._state == "paused"
        assert c.post("/api/player/stop").status_code == 204
        assert player._state == "idle"


def test_player_commands_noop_without_tv_playback(make_client):
    with make_client(TOUCH) as c:
        assert c.post("/api/player/stop").status_code == 204
        assert c.post("/api/player/seek", json={"delta": 5}).status_code == 204


def test_setup_complete_and_reset(make_client):
    with make_client(TOUCH) as c:
        assert c.post("/api/setup/complete").json()["setup_done"] is True
        assert c.get("/api/state").json()["setup_done"] is True
        assert c.post("/api/setup/reset").json()["setup_done"] is False


def test_bad_catalogue_still_boots(make_client, cfg):
    cfg.catalogue_path.write_text("{not json", encoding="utf-8")
    with make_client(TOUCH) as c:
        assert c.get("/api/pieces").json() == []
        assert c.app_state.catalogue_error


def test_views_flushed_on_shutdown(make_client, cfg):
    from pyrotv.store import Store

    with make_client(TOUCH) as c:
        c.post(f"/api/pieces/{KEY_ORN}/view")
    s = Store(cfg.db_path)
    try:
        assert s.views().get(KEY_ORN) == 1
    finally:
        s.close()


class _Outputs:
    connected = True


async def test_sse_first_event_and_published():
    bus = EventBus()
    gen = sse_stream(bus, _Outputs())
    first = await gen.__anext__()
    assert first == 'data: {"type":"tv","connected":true}\n\n'
    task = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0)
    bus.publish({"type": "stopped", "reason": "user"})
    chunk = await asyncio.wait_for(task, 1)
    assert chunk.startswith("data: ")
    assert json.loads(chunk[6:]) == {"type": "stopped", "reason": "user"}
    await gen.aclose()
    assert bus._subscribers == []


async def test_sse_keepalive():
    bus = EventBus()
    gen = sse_stream(bus, _Outputs(), keepalive=0.01)
    await gen.__anext__()
    assert await asyncio.wait_for(gen.__anext__(), 1) == ": keepalive\n\n"
    await gen.aclose()


def test_nfd_keys_are_normalised(make_client):
    import unicodedata

    nfd = unicodedata.normalize("NFD", KEY_ASKA)
    assert nfd != KEY_ASKA
    enc = quote(nfd, safe="")
    with make_client(TOUCH) as c:
        enable_all(c)
        assert c.get("/api/thumb/" + enc).status_code == 200
        assert c.get("/media/video/" + enc).status_code == 200
        assert c.post(f"/api/pieces/{enc}/view").status_code == 204
        assert c.get("/api/pieces").json()[1]["views"] == 1
        r = c.post("/api/play", json={"key": nfd})
        assert r.status_code == 200 and r.json()["key"] == KEY_ASKA
        r = c.put(f"/api/pieces/{enc}/enabled", json={"enabled": False})
        assert r.status_code == 200 and r.json()["key"] == KEY_ASKA
        assert KEY_ASKA not in [p["key"] for p in c.get("/api/pieces").json()]
