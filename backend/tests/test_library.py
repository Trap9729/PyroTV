from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from pyrotv.catalogue import Catalogue, Manufacturer, Piece, make_key
from pyrotv.config import Config
from pyrotv.library import Library, PieceView, sv_sort_key
from pyrotv.store import Store


def _cfg(tmp_path: Path) -> Config:
    cfg = Config(data_dir=tmp_path / "data", catalogue_path=tmp_path / "c.json", static_dir=tmp_path)
    cfg.ensure_dirs()
    return cfg


def _piece(tmp_path: Path, mid: str, name: str, category: str) -> Piece:
    video = tmp_path / f"{mid}-{name}.mp4"
    video.write_bytes(b"x")
    return Piece(make_key(mid, name), name, mid, category, video, None)


@pytest.fixture
def env(tmp_path):
    cfg = _cfg(tmp_path)
    pieces = [
        _piece(tmp_path, "a", "Zebra", "Tårtor"),
        _piece(tmp_path, "a", "Åska 49", "Batterier"),
        _piece(tmp_path, "a", "Apa", "Batterier"),
        _piece(tmp_path, "b", "Örn", "Raketer"),
    ]
    pieces[2] = dataclasses.replace(pieces[2], image=tmp_path / "img.jpg")
    cat = Catalogue(
        [
            Manufacturer("a", "A", ("Batterier", "Tårtor")),
            Manufacturer("b", "B", ("Raketer",)),
        ],
        pieces,
        [],
    )
    store = Store(tmp_path / "db.sqlite")
    store.set_enabled_manufacturers({"a"})
    yield cfg, cat, store, Library(cfg, cat, store)
    store.close()


def test_sv_sort_key():
    assert sorted(["Örn", "Åska", "Zebra", "Ärta", "Apa"], key=sv_sort_key) == [
        "Apa", "Zebra", "Åska", "Ärta", "Örn",
    ]


def test_default_hides_disabled_manufacturer_and_pieces(env):
    _, _, store, lib = env
    assert [p.name for p in lib.list()] == ["Apa", "Zebra", "Åska 49"]
    store.set_piece_enabled(make_key("a", "Zebra"), False)
    assert [p.name for p in lib.list()] == ["Apa", "Åska 49"]
    assert len(lib.list(include_disabled=True)) == 4


def test_pieceview_fields(env):
    _, _, store, lib = env
    store.add_view(make_key("a", "Apa"))
    v = lib.list(query="apa")[0]
    assert v == PieceView(make_key("a", "Apa"), "Apa", "a", "Batterier", True, False, 1)
    assert lib.list(query="zebra")[0].has_image is False


def test_sort_views(env):
    _, _, store, lib = env
    for _ in range(3):
        store.add_view(make_key("a", "Zebra"))
    store.add_view(make_key("a", "Åska 49"))
    assert [p.name for p in lib.list(sort="views")] == ["Zebra", "Åska 49", "Apa"]
    store.add_view(make_key("a", "Apa"))
    assert [p.name for p in lib.list(sort="views")] == ["Zebra", "Apa", "Åska 49"]


def test_category_and_query(env):
    _, _, _, lib = env
    assert [p.name for p in lib.list(category="Batterier")] == ["Apa", "Åska 49"]
    assert [p.name for p in lib.list(query="åsk")] == ["Åska 49"]
    assert [p.name for p in lib.list(query="ÅSK")] == ["Åska 49"]
    assert [p.name for p in lib.list(query="Åsk")] == ["Åska 49"]  # decomposed
    assert len(lib.list(query="   ")) == 3


def test_categories_union_of_visible(env):
    _, _, store, lib = env
    assert lib.categories() == ["Batterier", "Tårtor"]
    store.set_enabled_manufacturers({"a", "b"})
    assert lib.categories() == sorted(["Batterier", "Raketer", "Tårtor"], key=sv_sort_key)


def test_get_ignores_visibility(env):
    _, _, _, lib = env
    assert lib.get(make_key("b", "Örn")).name == "Örn"
    assert lib.get("nope") is None


def test_custom_under_builtin(env, tmp_path):
    cfg, _, store, lib = env
    store.set_enabled_manufacturers({"b"})
    rid = store.add_custom_piece("a", "Min raket", "Raketer", f"x.mp4", None)
    (cfg.custom_videos / f"{rid}.mp4").write_bytes(b"x")
    lib.reload_custom()
    assert [p.name for p in lib.list()] == ["Örn"]  # a not enabled
    store.set_enabled_manufacturers({"a"})
    views = lib.list()
    mine = [p for p in views if p.name == "Min raket"][0]
    assert mine.custom is True and mine.has_image is False
    p = lib.get(make_key("a", "Min raket"))
    assert p.video == cfg.custom_videos / f"{rid}.mp4" and p.image is None
    (cfg.custom_images / f"{rid}.jpg").write_bytes(b"x")
    lib.reload_custom()
    assert lib.get(make_key("a", "Min raket")).image == cfg.custom_images / f"{rid}.jpg"
    a = [m for m in lib.manufacturers() if m.id == "a"][0]
    assert a.categories == ("Batterier", "Tårtor", "Raketer")


def test_custom_manufacturer(env):
    cfg, _, store, lib = env
    mid = store.add_custom_manufacturer("Egen")
    rid = store.add_custom_piece(mid, "Smäll", "Övrigt", "x.mp4", None)
    store.add_custom_piece(mid, "Ingen video", "Övrigt", "y.mp4", None)
    (cfg.custom_videos / f"{rid}.mp4").write_bytes(b"x")
    lib.reload_custom()
    mans = lib.manufacturers()
    assert [m.id for m in mans] == ["a", "b", mid]
    assert mans[-1] == Manufacturer(mid, "Egen", ("Övrigt",))
    assert "Smäll" not in [p.name for p in lib.list()]
    assert "Smäll" in [p.name for p in lib.list(include_disabled=True)]
    store.set_enabled_manufacturers({"a", mid})
    assert "Smäll" in [p.name for p in lib.list()]
    assert "Ingen video" not in [p.name for p in lib.list(include_disabled=True)]
