from __future__ import annotations

from pathlib import Path

from conftest import OUTSIDER, PHONE


def fake_run_factory():
    calls = []

    def run(args):
        calls.append(list(args))
        return 0

    return run, calls


def test_hotspot_pin_lifecycle(make_client):
    run, calls = fake_run_factory()
    with make_client(hotspot_run=run) as c:
        body = c.get("/api/hotspot").json()
        assert body["on"] is False and body["pin"] is None and body["qr_svg"] is None
        assert body["url"] == "http://pyrotv.upload"
        assert body["ip_url"] == "http://10.42.0.1"
        assert body["url_qr_svg"] is None
        r = c.post("/api/hotspot", json={"on": True}).json()
        assert r["on"] is True and len(r["pin"]) == 8 and r["pin"].isdigit()
        assert r["qr_svg"].startswith("<svg")
        assert r["url_qr_svg"].startswith("<svg") and r["url_qr_svg"] != r["qr_svg"]
        assert c.get("/api/hotspot").json()["pin"] == r["pin"]
        r2 = c.post("/api/hotspot", json={"on": False}).json()
        assert r2["on"] is False and r2["pin"] is None
        assert c.get("/api/hotspot").json()["pin"] is None
        assert ["nmcli", "con", "down", "pyrotv-hotspot"] in calls


def test_hotspot_responses_are_not_cached(make_client):
    with make_client() as c:
        assert c.get("/api/hotspot").headers["cache-control"] == "no-store"
        assert c.post("/api/hotspot", json={"on": True}).headers["cache-control"] == "no-store"


def test_hotspot_reports_autooff_minutes(make_client):
    with make_client() as c:
        assert c.get("/api/hotspot").json()["autooff_minutes"] == 30
        assert c.post("/api/hotspot", json={"on": False}).json()["autooff_minutes"] == 30
        c.put("/api/settings", json={"hotspot_autooff_minutes": 0})
        assert c.get("/api/hotspot").json()["autooff_minutes"] == 0
        c.put("/api/settings", json={"hotspot_autooff_minutes": 120})
        assert c.post("/api/hotspot", json={"on": True}).json()["autooff_minutes"] == 120
        c.app_state.store.set_setting("hotspot_autooff_minutes", "junk")
        assert c.get("/api/hotspot").json()["autooff_minutes"] == 30


def test_hotspot_failure_is_502(make_client):
    with make_client(hotspot_run=lambda a: 0 if a[2] == "down" else 1) as c:
        r = c.post("/api/hotspot", json={"on": True})
        assert r.status_code == 502 and r.json() == {"detail": "hotspot_failed"}
        assert c.get("/api/hotspot").json()["on"] is False


def test_hotspot_brought_down_at_startup(make_client):
    run, calls = fake_run_factory()
    with make_client(hotspot_run=run):
        assert calls[0] == ["nmcli", "con", "down", "pyrotv-hotspot"]


def test_storage(make_client, cfg):
    with make_client() as c:
        d = c.get("/api/storage").json()
        assert d["used_bytes"] >= 3 * 1024  # three 1024-byte videos plus images
        assert set(d) == {"used_bytes", "budget_bytes", "free_bytes", "total_bytes"}
        (cfg.custom_videos / "x.mp4").write_bytes(b"x" * 5000)
        assert c.get("/api/storage").json()["used_bytes"] == d["used_bytes"] + 5000


def test_diagnostics_lists_missing(make_client, cfg):
    (cfg.media_videos / "Blixt.mp4").unlink()
    with make_client() as c:
        d = c.get("/api/diagnostics").json()
        assert d["missing_media"] == ["Blixt"]
        assert d["free_bytes"] > 0
        assert d["catalogue_error"] is None


def test_backup_roundtrip_via_routes(make_client, tmp_path):
    usb_root = tmp_path / "media"
    (usb_root / "pi" / "STICK").mkdir(parents=True)
    with make_client() as c:
        c.app_state.usb_root = usb_root
        c.app_state.usb_is_mount = lambda p: p.name == "STICK"
        c.put("/api/settings", json={"shop_name": "Original"})
        r = c.post("/api/backup/export")
        assert r.status_code == 200
        path = Path(r.json()["path"])
        assert path == usb_root / "pi" / "STICK" / "pyrotv-backup.json"
        c.put("/api/settings", json={"shop_name": "Changed"})
        assert c.post("/api/backup/import").json() == {"imported": True}
        assert c.get("/api/state").json()["shop_name"] == "Original"


def test_backup_errors(make_client, tmp_path):
    usb_root = tmp_path / "media"
    (usb_root / "STICK").mkdir(parents=True)
    with make_client() as c:
        c.app_state.usb_root = usb_root
        c.app_state.usb_is_mount = lambda p: False
        for ep in ("export", "import"):
            r = c.post(f"/api/backup/{ep}")
            assert r.status_code == 409 and r.json() == {"detail": "no_usb"}
        c.app_state.usb_is_mount = lambda p: True
        r = c.post("/api/backup/import")
        assert r.status_code == 404 and r.json() == {"detail": "no_backup"}
        (usb_root / "STICK" / "pyrotv-backup.json").write_text("garbage")
        r = c.post("/api/backup/import")
        assert r.status_code == 422 and r.json() == {"detail": "invalid_backup"}


def test_phone_allowed_outsider_forbidden(make_client):
    with make_client(PHONE) as c:
        assert c.get("/api/storage").status_code == 200
    with make_client(OUTSIDER) as c:
        for method, url in [
            ("get", "/api/storage"),
            ("get", "/api/hotspot"),
            ("post", "/api/hotspot"),
            ("post", "/api/backup/export"),
            ("post", "/api/backup/import"),
            ("get", "/api/diagnostics"),
        ]:
            assert getattr(c, method)(url).status_code == 403, url
