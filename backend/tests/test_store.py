import sqlite3

import pytest

from pyrotv.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def test_settings_roundtrip(store):
    assert store.get_setting("shop_name") is None
    assert store.get_setting("shop_name", "x") == "x"
    store.set_setting("shop_name", "Pyro")
    assert store.get_setting("shop_name") == "Pyro"
    store.set_setting("shop_name", "Other")
    assert store.get_setting("shop_name") == "Other"


def test_pragmas(store):
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store._conn.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_views_pending_until_flush(store, tmp_path):
    for _ in range(3):
        store.add_view("a:x")
    raw = sqlite3.connect(tmp_path / "t.db")
    try:
        assert raw.execute("SELECT count(*) FROM views").fetchone()[0] == 0
        assert store.views()["a:x"] == 3
        store.flush_views()
        assert raw.execute("SELECT count FROM views WHERE piece_key='a:x'").fetchone()[0] == 3
    finally:
        raw.close()
    assert store.views()["a:x"] == 3
    store.add_view("a:x")
    assert store.views()["a:x"] == 4
    store.flush_views()
    assert store.views()["a:x"] == 4


def test_enabled_manufacturers(store):
    assert store.enabled_manufacturers() == set()
    store.set_enabled_manufacturers(["a", "b"])
    assert store.enabled_manufacturers() == {"a", "b"}
    store.set_enabled_manufacturers(["b"])
    assert store.enabled_manufacturers() == {"b"}


def test_disabled_pieces_toggle(store):
    store.set_piece_enabled("a:x", False)
    assert store.disabled_pieces() == {"a:x"}
    store.set_piece_enabled("a:x", False)
    assert store.disabled_pieces() == {"a:x"}
    store.set_piece_enabled("a:x", True)
    assert store.disabled_pieces() == set()


def test_custom_manufacturer_id(store):
    assert store.add_custom_manufacturer("Min Tillverkare") == "c-min-tillverkare"
    with pytest.raises(ValueError):
        store.add_custom_manufacturer("Min Tillverkare")
    assert store.add_custom_manufacturer("  Åäö  Fyr!  ") == "c-åäö-fyr"
    with pytest.raises(ValueError):
        store.add_custom_manufacturer("!!!")
    names = {m.id: m.name for m in store.list_custom_manufacturers()}
    assert names["c-min-tillverkare"] == "Min Tillverkare"


def test_custom_piece_roundtrip(store):
    mid = store.add_custom_manufacturer("Min Tillverkare")
    pid = store.add_custom_piece(mid, "Raket", "rockets", "v.mp4", "i.jpg")
    pid2 = store.add_custom_piece(mid, "Bomb", "cakes", "v2.mp4", None)
    rows = store.list_custom_pieces()
    assert [r.id for r in rows] == [pid, pid2]
    assert rows[0].manufacturer_id == mid
    assert rows[0].image_file == "i.jpg" and rows[1].image_file is None
    store.update_custom_piece(pid, "Raket2", "cakes")
    r = [x for x in store.list_custom_pieces() if x.id == pid][0]
    assert (r.name, r.category) == ("Raket2", "cakes")
    deleted = store.delete_custom_piece(pid)
    assert deleted is not None and deleted.id == pid and deleted.video_file == "v.mp4"
    assert store.delete_custom_piece(pid) is None
    assert [r.id for r in store.list_custom_pieces()] == [pid2]


def test_delete_last_piece_removes_manufacturer(store):
    mid = store.add_custom_manufacturer("Alfa")
    store.set_enabled_manufacturers([mid, "real"])
    p1 = store.add_custom_piece(mid, "A", "c", "a.mp4", None)
    p2 = store.add_custom_piece(mid, "B", "c", "b.mp4", None)
    store.delete_custom_piece(p1)
    assert [m.id for m in store.list_custom_manufacturers()] == [mid]
    assert mid in store.enabled_manufacturers()
    store.delete_custom_piece(p2)
    assert store.list_custom_manufacturers() == []
    assert store.enabled_manufacturers() == {"real"}


def test_export_import_roundtrip(store, tmp_path):
    store.set_setting("shop_name", "Pyro")
    store.set_setting("setup_done", "1")
    store.set_enabled_manufacturers(["a", "b"])
    store.set_piece_enabled("a:x", False)
    store.add_view("a:y")
    store.add_view("a:y")
    store.flush_views()
    store.add_view("a:y")
    mid = store.add_custom_manufacturer("Min")
    store.add_custom_piece(mid, "P", "c", "v.mp4", None)
    data = store.export_state()
    assert "custom_pieces" not in data

    other = Store(tmp_path / "other.db")
    try:
        other.import_state(data)
        assert other.get_setting("shop_name") == "Pyro"
        assert other.get_setting("setup_done") == "1"
        assert other.enabled_manufacturers() == {"a", "b"}
        assert other.disabled_pieces() == {"a:x"}
        assert other.views() == {"a:y": 3}
        assert other.list_custom_pieces() == []
    finally:
        other.close()
