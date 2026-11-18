from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from pyrotv.catalogue import (
    Catalogue,
    CatalogueError,
    Manufacturer,
    load_catalogue,
    make_key,
)


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def nfd(s: str) -> str:
    return unicodedata.normalize("NFD", s)


def write_cat(tmp_path: Path, data: object) -> Path:
    p = tmp_path / "catalogue.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def cat_data(pieces=None, categories=None, mid="lesli", version=1):
    return {
        "version": version,
        "manufacturers": [
            {
                "id": mid,
                "name": "Lesli",
                "categories": categories if categories is not None else ["Tårtor", "Singelskott"],
                "pieces": pieces if pieces is not None else [{"name": "Åska 49", "category": "Tårtor"}],
            }
        ],
    }


@pytest.fixture
def dirs(tmp_path):
    v = tmp_path / "videos"
    i = tmp_path / "images"
    v.mkdir()
    i.mkdir()
    return v, i


def test_make_key_normalises_name():
    assert make_key("lesli", nfd("Åska 49")) == "lesli:" + nfc("Åska 49")


def test_nfd_filename_matches_nfc_name(tmp_path, dirs):
    v, i = dirs
    (v / (nfd("Åska 49") + ".mp4")).write_bytes(b"x")
    cat = load_catalogue(write_cat(tmp_path, cat_data()), v, i)
    assert isinstance(cat, Catalogue)
    assert [p.name for p in cat.pieces] == ["Åska 49"]
    p = cat.pieces[0]
    assert p.key == make_key("lesli", "Åska 49")
    assert p.manufacturer_id == "lesli"
    assert p.category == "Tårtor"
    assert p.video is not None and p.video.parent == v
    assert p.custom is False
    assert cat.missing == []
    assert cat.manufacturers == [Manufacturer("lesli", "Lesli", ("Tårtor", "Singelskott"))]


def test_case_insensitive_and_extension(tmp_path, dirs):
    v, i = dirs
    (v / "åska 49.MP4").write_bytes(b"x")
    cat = load_catalogue(write_cat(tmp_path, cat_data()), v, i)
    assert len(cat.pieces) == 1


def test_non_mp4_not_matched(tmp_path, dirs):
    v, i = dirs
    (v / "Åska 49.mkv").write_bytes(b"x")
    cat = load_catalogue(write_cat(tmp_path, cat_data()), v, i)
    assert cat.pieces == []
    assert cat.missing == ["Åska 49"]


def test_missing_video_excluded_and_reported(tmp_path, dirs):
    v, i = dirs
    data = cat_data(
        pieces=[
            {"name": "Åska 49", "category": "Tårtor"},
            {"name": "Sprakande Örn", "category": "Singelskott"},
        ]
    )
    (v / "Sprakande Örn.mp4").write_bytes(b"x")
    cat = load_catalogue(write_cat(tmp_path, data), v, i)
    assert [p.name for p in cat.pieces] == ["Sprakande Örn"]
    assert cat.missing == ["Åska 49"]


def test_missing_directories_treated_as_empty(tmp_path):
    cat = load_catalogue(write_cat(tmp_path, cat_data()), tmp_path / "nov", tmp_path / "noi")
    assert cat.pieces == []
    assert cat.missing == ["Åska 49"]


def test_video_without_image_kept(tmp_path, dirs):
    v, i = dirs
    (v / "Åska 49.mp4").write_bytes(b"x")
    cat = load_catalogue(write_cat(tmp_path, cat_data()), v, i)
    assert cat.pieces[0].image is None


def test_png_accepted_and_jpg_preferred(tmp_path, dirs):
    v, i = dirs
    (v / "Åska 49.mp4").write_bytes(b"x")
    (i / "Åska 49.png").write_bytes(b"x")
    cat = load_catalogue(write_cat(tmp_path, cat_data()), v, i)
    assert cat.pieces[0].image is not None
    assert cat.pieces[0].image.suffix == ".png"
    (i / "Åska 49.jpg").write_bytes(b"x")
    cat = load_catalogue(write_cat(tmp_path, cat_data()), v, i)
    assert cat.pieces[0].image.suffix == ".jpg"


def test_invalid_json(tmp_path, dirs):
    p = tmp_path / "catalogue.json"
    p.write_text("{nope", encoding="utf-8")
    with pytest.raises(CatalogueError):
        load_catalogue(p, *dirs)


def test_missing_catalogue_file(tmp_path, dirs):
    with pytest.raises(CatalogueError):
        load_catalogue(tmp_path / "absent.json", *dirs)


def test_unknown_version(tmp_path, dirs):
    with pytest.raises(CatalogueError):
        load_catalogue(write_cat(tmp_path, cat_data(version=2)), *dirs)


def test_category_not_in_manufacturer(tmp_path, dirs):
    data = cat_data(pieces=[{"name": "X", "category": "Raketer"}])
    with pytest.raises(CatalogueError):
        load_catalogue(write_cat(tmp_path, data), *dirs)


def test_duplicate_name_within_manufacturer(tmp_path, dirs):
    data = cat_data(
        pieces=[
            {"name": "Åska 49", "category": "Tårtor"},
            {"name": nfd("åska 49"), "category": "Singelskott"},
        ]
    )
    with pytest.raises(CatalogueError):
        load_catalogue(write_cat(tmp_path, data), *dirs)


def test_same_name_in_different_manufacturers_ok(tmp_path, dirs):
    v, i = dirs
    (v / "Åska 49.mp4").write_bytes(b"x")
    data = cat_data()
    data["manufacturers"].append(
        {
            "id": "nico",
            "name": "Nico",
            "categories": ["Tårtor"],
            "pieces": [{"name": "Åska 49", "category": "Tårtor"}],
        }
    )
    cat = load_catalogue(write_cat(tmp_path, data), v, i)
    assert [p.key for p in cat.pieces] == ["lesli:Åska 49", "nico:Åska 49"]


def test_reserved_custom_prefix_rejected(tmp_path, dirs):
    with pytest.raises(CatalogueError):
        load_catalogue(write_cat(tmp_path, cat_data(mid="c-lesli")), *dirs)


def test_duplicate_manufacturer_id_rejected(tmp_path, dirs):
    data = cat_data()
    data["manufacturers"].append(dict(data["manufacturers"][0]))
    with pytest.raises(CatalogueError):
        load_catalogue(write_cat(tmp_path, data), *dirs)


def test_malformed_structure_rejected(tmp_path, dirs):
    with pytest.raises(CatalogueError):
        load_catalogue(write_cat(tmp_path, {"version": 1}), *dirs)
    with pytest.raises(CatalogueError):
        load_catalogue(write_cat(tmp_path, [1, 2]), *dirs)
    with pytest.raises(CatalogueError):
        load_catalogue(write_cat(tmp_path, cat_data(pieces=[{"name": "X"}])), *dirs)
