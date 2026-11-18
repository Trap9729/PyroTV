from __future__ import annotations

import shutil
import time
import unicodedata
from pathlib import Path

import pytest

from pyrotv.catalogue import Catalogue, Manufacturer, Piece, make_key
from pyrotv.config import Config
from pyrotv.customs import (
    CustomService,
    DuplicateName,
    UnknownManufacturer,
    cleanup_custom_orphans,
)
from pyrotv.library import Library
from pyrotv.store import Store
from pyrotv.uploads import IncompleteUpload, UploadManager

BIG = 1 << 40


def copy_video(src: Path, dest: Path) -> None:
    shutil.copyfile(src, dest)


def copy_image(src: Path, dest: Path) -> None:
    shutil.copyfile(src, dest)


@pytest.fixture
def env(tmp_path):
    cfg = Config(data_dir=tmp_path / "data", catalogue_path=tmp_path / "c.json", static_dir=tmp_path)
    cfg.ensure_dirs()
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"v")
    cat = Catalogue(
        [Manufacturer("a", "Alfa", ("Batterier",)), Manufacturer("b", "Beta", ("Raketer",))],
        [
            Piece(make_key("a", "Åska 49"), "Åska 49", "a", "Batterier", vid, None),
            Piece(make_key("b", "Blixt"), "Blixt", "b", "Raketer", vid, None),
        ],
        [],
    )
    store = Store(tmp_path / "db.sqlite")
    store.set_enabled_manufacturers({"a"})
    lib = Library(cfg, cat, store)
    up = UploadManager(cfg, free_bytes=lambda p: BIG)
    svc = CustomService(cfg, store, lib, up, process_video=copy_video, process_image=copy_image)
    yield cfg, store, lib, up, svc
    svc.close()
    store.close()


def upload(up: UploadManager, kind: str, data: bytes = b"data") -> str:
    ext = "mp4" if kind == "video" else "png"
    info = up.create(kind, f"f.{ext}", len(data))
    up.write_chunk(info.id, 0, data)
    return info.id


def wait(svc: CustomService, job_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        j = svc.job(job_id)
        if j["state"] != "processing":
            return j
        time.sleep(0.01)
    raise AssertionError("job stuck")


def create(svc, up, **kw):
    args = dict(
        manufacturer_id="a",
        new_manufacturer_name=None,
        name="Min raket",
        category="Batterier",
        video_upload_id=upload(up, "video"),
        image_upload_id=None,
    )
    args.update(kw)
    return svc.create(**args)


@pytest.mark.parametrize(
    "name",
    ["åska 49", "ÅSKA 49", unicodedata.normalize("NFD", "Åska 49"), "  Åska 49 "],
)
def test_duplicate_name_same_manufacturer(env, name):
    _, _, _, up, svc = env
    with pytest.raises(DuplicateName):
        create(svc, up, name=name)


def test_duplicate_checks_hidden_pieces_and_other_manufacturer_ok(env):
    _, store, _, up, svc = env
    store.set_piece_enabled(make_key("a", "Åska 49"), False)
    with pytest.raises(DuplicateName):
        create(svc, up, name="åska 49")
    # same name under another manufacturer is fine
    job = create(svc, up, manufacturer_id="b", name="Åska 49", category="Raketer")
    assert wait(svc, job)["state"] == "done"


def test_create_under_builtin(env):
    cfg, store, lib, up, svc = env
    img = upload(up, "image", b"img")
    job = create(svc, up, image_upload_id=img)
    assert wait(svc, job) == {"state": "done", "message": None}
    p = lib.get(make_key("a", "Min raket"))
    assert p is not None and p.custom and p.image is not None
    assert p.video.parent == cfg.custom_videos and p.video.suffix == ".mp4"
    assert p.image.parent == cfg.custom_images and p.image.suffix == ".jpg"
    assert any(v.key == p.key and v.custom for v in lib.list())
    row = store.list_custom_pieces()[0]
    assert row.video_file == p.video.name and row.image_file == p.image.name
    assert list(cfg.upload_tmp_dir.iterdir()) == []


def test_create_new_manufacturer(env):
    _, store, lib, up, svc = env
    job = create(svc, up, manufacturer_id=None, new_manufacturer_name=" Min Tillverkare ")
    assert wait(svc, job)["state"] == "done"
    assert "c-min-tillverkare" in store.enabled_manufacturers()
    assert "a" in store.enabled_manufacturers()
    assert [m.id for m in lib.manufacturers()][-1] == "c-min-tillverkare"
    assert [v.name for v in lib.list() if v.manufacturer_id == "c-min-tillverkare"] == ["Min raket"]


def test_new_manufacturer_matching_existing_name_reuses_it(env):
    _, store, lib, up, svc = env
    job = create(svc, up, manufacturer_id=None, new_manufacturer_name="alfa")
    assert wait(svc, job)["state"] == "done"
    assert store.list_custom_manufacturers() == []
    assert lib.get(make_key("a", "Min raket")) is not None
    # and a custom manufacturer is reused too
    job = create(svc, up, manufacturer_id=None, new_manufacturer_name="Egen", name="X1", category="K")
    assert wait(svc, job)["state"] == "done"
    job = create(svc, up, manufacturer_id=None, new_manufacturer_name="EGEN", name="X2", category="K")
    assert wait(svc, job)["state"] == "done"
    assert len(store.list_custom_manufacturers()) == 1


def test_manufacturer_arguments(env):
    _, _, _, up, svc = env
    with pytest.raises(ValueError):
        create(svc, up, manufacturer_id="a", new_manufacturer_name="X")
    with pytest.raises(ValueError):
        create(svc, up, manufacturer_id=None, new_manufacturer_name=None)
    with pytest.raises(UnknownManufacturer):
        create(svc, up, manufacturer_id="zzz")


def test_field_validation(env):
    _, _, _, up, svc = env
    for kw in (
        {"name": "   "},
        {"name": "x" * 81},
        {"category": ""},
        {"category": "c" * 41},
        {"manufacturer_id": None, "new_manufacturer_name": "m" * 41},
        {"manufacturer_id": None, "new_manufacturer_name": "  "},
        {"manufacturer_id": None, "new_manufacturer_name": "!!!"},
    ):
        with pytest.raises(ValueError):
            create(svc, up, **kw)


def test_unknown_or_incomplete_upload(env):
    _, _, _, up, svc = env
    with pytest.raises(KeyError):
        create(svc, up, video_upload_id="0" * 32)
    info = up.create("video", "a.mp4", 100)
    with pytest.raises(IncompleteUpload):
        create(svc, up, video_upload_id=info.id)


def test_failed_job_leaves_nothing(env):
    cfg, store, lib, up, _ = env

    def bad_image(src, dest):
        raise RuntimeError("image broke")

    svc = CustomService(cfg, store, lib, up, process_video=copy_video, process_image=bad_image)
    try:
        job = create(
            svc, up, manufacturer_id=None, new_manufacturer_name="Ny",
            image_upload_id=upload(up, "image"),
        )
        res = wait(svc, job)
        assert res["state"] == "error" and "image broke" in res["message"]
        assert store.list_custom_pieces() == [] and store.list_custom_manufacturers() == []
        assert store.enabled_manufacturers() == {"a"}
        assert list(cfg.custom_videos.iterdir()) == [] and list(cfg.custom_images.iterdir()) == []
        assert list(cfg.upload_tmp_dir.iterdir()) == []
    finally:
        svc.close()


def test_unknown_job(env):
    with pytest.raises(KeyError):
        env[4].job("nope")


def test_delete_removes_row_and_files_and_manufacturer(env):
    cfg, store, lib, up, svc = env
    job = create(svc, up, manufacturer_id=None, new_manufacturer_name="Ny", image_upload_id=upload(up, "image"))
    assert wait(svc, job)["state"] == "done"
    pid = svc.list()[0]["id"]
    assert svc.list()[0]["has_image"] is True
    svc.delete(pid)
    assert store.list_custom_pieces() == []
    assert list(cfg.custom_videos.iterdir()) == [] and list(cfg.custom_images.iterdir()) == []
    assert "c-ny" not in [m.id for m in lib.manufacturers()]
    svc.delete(pid)  # idempotent


def test_update(env):
    _, _, lib, up, svc = env
    assert wait(svc, create(svc, up))["state"] == "done"
    assert wait(svc, create(svc, up, name="Annan"))["state"] == "done"
    pid = svc.list()[0]["id"]
    svc.update(pid, "  Ny   ", "Batterier")
    assert lib.get(make_key("a", "Ny")) is not None and lib.get(make_key("a", "Min raket")) is None
    svc.update(pid, "NY", "Batterier")  # itself is excluded from the duplicate check
    with pytest.raises(DuplicateName):
        svc.update(pid, "annan", "Batterier")
    with pytest.raises(DuplicateName):
        svc.update(pid, "åska 49", "Batterier")
    with pytest.raises(ValueError):
        svc.update(pid, "", "Batterier")
    with pytest.raises(KeyError):
        svc.update(9999, "Z", "Batterier")


def test_cleanup_custom_orphans(env):
    cfg, store, lib, up, svc = env
    assert wait(svc, create(svc, up, image_upload_id=upload(up, "image")))["state"] == "done"
    row = store.list_custom_pieces()[0]
    (cfg.custom_videos / "orphan.mp4").write_bytes(b"x")
    (cfg.custom_images / "orphan.jpg").write_bytes(b"x")
    (cfg.custom_videos / "half.mp4.part").write_bytes(b"x")
    (cfg.upload_tmp_dir / "stale.part").write_bytes(b"x")
    removed = cleanup_custom_orphans(cfg, store)
    assert removed >= 3
    assert sorted(p.name for p in cfg.custom_videos.iterdir()) == [row.video_file]
    assert sorted(p.name for p in cfg.custom_images.iterdir()) == [row.image_file]
    assert list(cfg.upload_tmp_dir.iterdir()) == []


def test_failing_piece_insert_leaves_nothing(env, monkeypatch):
    cfg, store, lib, up, svc = env
    # force the final INSERT to fail inside the transaction, after manufacturer + enabled rows
    orig = store._conn

    class Conn:
        def __getattr__(self, n):
            return getattr(orig, n)

        def __enter__(self):
            return orig.__enter__()

        def __exit__(self, *a):
            return orig.__exit__(*a)

        def execute(self, sql, *a):
            if sql.startswith("INSERT INTO custom_pieces"):
                raise RuntimeError("insert failed")
            return orig.execute(sql, *a)

    monkeypatch.setattr(store, "_conn", Conn())
    job = create(svc, up, manufacturer_id=None, new_manufacturer_name="Ny")
    res = wait(svc, job)
    assert res["state"] == "error"
    monkeypatch.setattr(store, "_conn", orig)
    assert store.list_custom_manufacturers() == [] and store.list_custom_pieces() == []
    assert store.enabled_manufacturers() == {"a"}
    assert list(cfg.custom_videos.iterdir()) == []


def test_reload_failure_after_commit_keeps_files_and_job_done(env, monkeypatch):
    cfg, store, lib, up, svc = env
    calls = {"n": 0}
    orig = lib.reload_custom

    def flaky():
        calls["n"] += 1
        raise RuntimeError("reload broke")

    monkeypatch.setattr(lib, "reload_custom", flaky)
    job = create(svc, up)
    assert wait(svc, job)["state"] == "done"
    monkeypatch.setattr(lib, "reload_custom", orig)
    row = store.list_custom_pieces()[0]
    assert (cfg.custom_videos / row.video_file).is_file()
    assert list(cfg.upload_tmp_dir.iterdir()) == []


def test_boot_cleanup_removes_empty_custom_manufacturer(env):
    cfg, store, lib, up, svc = env
    assert wait(svc, create(svc, up, manufacturer_id=None, new_manufacturer_name="Full"))["state"] == "done"
    empty = store.add_custom_manufacturer("Tom")
    store.set_enabled_manufacturers(store.enabled_manufacturers() | {empty})
    assert cleanup_custom_orphans(cfg, store) >= 1
    assert [m.id for m in store.list_custom_manufacturers()] == ["c-full"]
    assert store.enabled_manufacturers() == {"a", "c-full"}
