from __future__ import annotations

import shutil
import time
from urllib.parse import quote

import pytest

from conftest import OUTSIDER, PHONE, TOUCH
from pyrotv.catalogue import make_key

STUBS = (lambda s, d: shutil.copyfile(s, d), lambda s, d: shutil.copyfile(s, d))


def failing_video(src, dest):
    raise RuntimeError("encoder exploded")


def put_all(c, kind, filename, data: bytes, chunk: int = 700) -> str:
    r = c.post("/api/uploads", json={"kind": kind, "filename": filename, "size": len(data)})
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    for off in range(0, len(data), chunk):
        r = c.put(f"/api/uploads/{uid}", params={"offset": off}, content=data[off : off + chunk])
        assert r.status_code == 200, r.text
        assert r.json() == {"received": min(off + chunk, len(data))}
    r = c.post(f"/api/uploads/{uid}/finish")
    assert r.status_code == 200 and r.json() == {"received": len(data), "size": len(data)}
    return uid


def wait_job(c, job_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        j = c.get(f"/api/custom/jobs/{job_id}").json()
        if j["state"] != "processing":
            return j
        time.sleep(0.02)
    raise AssertionError("job stuck")


def test_outsider_403_on_every_upload_and_custom_route(make_client):
    with make_client(OUTSIDER, custom_process=STUBS) as c:
        calls = [
            ("post", "/api/uploads"),
            ("get", "/api/uploads/" + "a" * 32),
            ("put", "/api/uploads/" + "a" * 32 + "?offset=0"),
            ("post", "/api/uploads/" + "a" * 32 + "/finish"),
            ("post", "/api/custom"),
            ("get", "/api/custom/jobs/x"),
            ("get", "/api/custom"),
            ("patch", "/api/custom/1"),
            ("delete", "/api/custom/1"),
        ]
        for method, path in calls:
            r = getattr(c, method)(path)
            assert r.status_code == 403, (method, path)
            assert r.json() == {"detail": "forbidden"}


def test_happy_path_builtin_manufacturer(make_client):
    with make_client(PHONE, custom_process=STUBS) as c:
        c.put("/api/settings", json={"enabled_manufacturers": ["lesli"]})
        vid = put_all(c, "video", "show.MOV", b"v" * 2000)
        img = put_all(c, "image", "pic.png", b"i" * 900)
        r = c.post(
            "/api/custom",
            json={"manufacturer_id": "lesli", "name": " Egen ", "category": "Batterier",
                  "video_upload_id": vid, "image_upload_id": img},
        )
        assert r.status_code == 202
        assert wait_job(c, r.json()["job_id"]) == {"state": "done", "message": None}
        key = make_key("lesli", "Egen")
        pieces = {p["key"]: p for p in c.get("/api/pieces").json()}
        assert pieces[key]["custom"] is True and pieces[key]["has_image"] is True
        assert c.get("/media/video/" + quote(key, safe="")).content == b"v" * 2000
        listed = c.get("/api/custom").json()
        assert [(x["name"], x["manufacturer_id"], x["key"], x["has_image"]) for x in listed] == [
            ("Egen", "lesli", key, True)
        ]
        pid = listed[0]["id"]
        assert c.patch(f"/api/custom/{pid}", json={"name": "Nytt", "category": "Åska"}).status_code == 200
        assert make_key("lesli", "Nytt") in {p["key"] for p in c.get("/api/pieces").json()}
        assert c.patch(f"/api/custom/{pid}", json={"name": "blixt", "category": "x"}).status_code == 409
        assert c.patch("/api/custom/9999", json={"name": "q", "category": "x"}).status_code == 404
        assert c.patch(f"/api/custom/{pid}", json={"name": " ", "category": "x"}).status_code == 422
        assert c.delete(f"/api/custom/{pid}").status_code == 204
        assert c.get("/api/custom").json() == []


def test_new_manufacturer_via_api(make_client):
    with make_client(TOUCH, custom_process=STUBS) as c:
        vid = put_all(c, "video", "a.mp4", b"v" * 100)
        r = c.post(
            "/api/custom",
            json={"new_manufacturer_name": "Min Tillverkare", "name": "X", "category": "Övrigt",
                  "video_upload_id": vid},
        )
        assert r.status_code == 202
        assert wait_job(c, r.json()["job_id"])["state"] == "done"
        mans = {m["id"]: m for m in c.get("/api/manufacturers").json()}
        assert mans["c-min-tillverkare"]["enabled"] is True and mans["c-min-tillverkare"]["custom"]


def test_error_mapping(make_client):
    with make_client(TOUCH, custom_process=STUBS) as c:
        enable = c.put("/api/settings", json={"enabled_manufacturers": ["lesli"]})
        assert enable.status_code == 200
        vid = put_all(c, "video", "a.mp4", b"v" * 10)
        base = {"manufacturer_id": "lesli", "name": "N", "category": "C", "video_upload_id": vid}
        assert c.post("/api/custom", json={**base, "name": "åska 49"}).status_code == 409
        assert c.post("/api/custom", json={**base, "manufacturer_id": "nope"}).status_code == 404
        assert c.post("/api/custom", json={**base, "new_manufacturer_name": "X"}).status_code == 422
        assert c.post("/api/custom", json={**base, "name": ""}).status_code == 422
        assert c.post("/api/custom", json={**base, "video_upload_id": "0" * 32}).status_code == 404
        r = c.post("/api/uploads", json={"kind": "video", "filename": "b.mp4", "size": 50})
        inc = r.json()["id"]
        assert c.post("/api/custom", json={**base, "video_upload_id": inc}).status_code == 409
        assert c.get("/api/custom/jobs/nope").status_code == 404


def test_upload_route_errors(make_client):
    with make_client(TOUCH, custom_process=STUBS) as c:
        assert c.post("/api/uploads", json={"kind": "video", "filename": "a.exe", "size": 5}).status_code == 415
        assert c.post("/api/uploads", json={"kind": "video", "filename": "a.mp4", "size": 0}).status_code == 413
        assert c.post("/api/uploads", json={"kind": "image", "filename": "a.png", "size": 60 * 2**20}).status_code == 413
        assert c.get("/api/uploads/" + "0" * 32).status_code == 404
        assert c.get("/api/uploads/..%2Fx").status_code == 404
        uid = c.post("/api/uploads", json={"kind": "video", "filename": "a.mp4", "size": 100}).json()["id"]
        assert c.get(f"/api/uploads/{uid}").json()["received"] == 0
        assert c.put(f"/api/uploads/{uid}", params={"offset": 0}, content=b"x" * 40).status_code == 200
        r = c.put(f"/api/uploads/{uid}", params={"offset": 0}, content=b"x" * 40)
        assert r.status_code == 409 and r.json() == {"received": 40}
        r = c.post(f"/api/uploads/{uid}/finish")
        assert r.status_code == 409 and r.json() == {"received": 40, "size": 100}
        big = c.put(f"/api/uploads/{uid}", params={"offset": 40}, content=b"x" * (16 * 2**20 + 1))
        assert big.status_code == 413
        assert c.put("/api/uploads/" + "0" * 32, params={"offset": 0}, content=b"x").status_code == 404


def test_chunk_disk_full_is_507(make_client):
    from pyrotv.uploads import DiskFull

    with make_client(TOUCH, custom_process=STUBS) as c:
        uid = c.post("/api/uploads", json={"kind": "video", "filename": "a.mp4", "size": 100}).json()["id"]

        def full(*a):
            raise DiskFull("not enough free disk space")

        c.app_state.uploads.write_chunk = full
        r = c.put(f"/api/uploads/{uid}", params={"offset": 0}, content=b"x" * 40)
        assert r.status_code == 507 and r.json() == {"detail": "not enough free disk space"}


def test_failed_job_via_api_leaves_nothing(make_client, cfg):
    with make_client(TOUCH, custom_process=(failing_video, STUBS[1])) as c:
        vid = put_all(c, "video", "a.mp4", b"v" * 100)
        r = c.post(
            "/api/custom",
            json={"new_manufacturer_name": "Ny", "name": "X", "category": "C", "video_upload_id": vid},
        )
        res = wait_job(c, r.json()["job_id"])
        assert res["state"] == "error" and "encoder exploded" in res["message"]
        assert c.get("/api/custom").json() == []
        assert "c-ny" not in [m["id"] for m in c.get("/api/manufacturers").json()]
        assert list(cfg.custom_videos.iterdir()) == []
        assert list(cfg.upload_tmp_dir.iterdir()) == []


def test_boot_cleanup_removes_stale_files(make_client, cfg):
    cfg.ensure_dirs()
    (cfg.upload_tmp_dir / "stale.part").write_bytes(b"x")
    (cfg.custom_videos / "orphan.mp4").write_bytes(b"x")
    (cfg.custom_videos / "half.mp4.part").write_bytes(b"x")
    with make_client(TOUCH, custom_process=STUBS):
        pass
    assert list(cfg.upload_tmp_dir.iterdir()) == []
    assert list(cfg.custom_videos.iterdir()) == []


@pytest.mark.parametrize("path", ["/api/uploads/..%2F..%2Fetc", "/api/uploads/%2e%2e"])
def test_traversal_ids_are_404(make_client, path):
    with make_client(TOUCH, custom_process=STUBS) as c:
        assert c.get(path).status_code == 404
