from __future__ import annotations

import re
import sqlite3
import threading
import unicodedata
from pathlib import Path
from typing import Iterable, NamedTuple


class CustomRow(NamedTuple):
    id: int
    manufacturer_id: str
    name: str
    category: str
    video_file: str
    image_file: str | None


class CustomManufacturer(NamedTuple):
    id: str
    name: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS enabled_manufacturers (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS disabled_pieces (piece_key TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS views (piece_key TEXT PRIMARY KEY, count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS custom_manufacturers (id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS custom_pieces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manufacturer_id TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    video_file TEXT NOT NULL,
    image_file TEXT
);
"""

_PIECE_COLS = "id, manufacturer_id, name, category, video_file, image_file"


def _slug(name: str) -> str:
    s = unicodedata.normalize("NFC", name).lower()
    s = re.sub(r"\s+", "-", s.strip())
    s = "".join(ch for ch in s if ch == "-" or ch.isalpha() or ch.isdigit())
    return re.sub(r"-+", "-", s).strip("-")


class Store:
    def __init__(self, db_path: Path) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, int] = {}
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    # settings
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # manufacturers / pieces
    def enabled_manufacturers(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute("SELECT id FROM enabled_manufacturers").fetchall()
        return {r[0] for r in rows}

    def set_enabled_manufacturers(self, ids: Iterable[str]) -> None:
        ids = set(ids)
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM enabled_manufacturers")
            self._conn.executemany(
                "INSERT INTO enabled_manufacturers(id) VALUES(?)", [(i,) for i in ids]
            )

    def disabled_pieces(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute("SELECT piece_key FROM disabled_pieces").fetchall()
        return {r[0] for r in rows}

    def set_piece_enabled(self, piece_key: str, enabled: bool) -> None:
        with self._lock, self._conn:
            if enabled:
                self._conn.execute(
                    "DELETE FROM disabled_pieces WHERE piece_key=?", (piece_key,)
                )
            else:
                self._conn.execute(
                    "INSERT OR IGNORE INTO disabled_pieces(piece_key) VALUES(?)",
                    (piece_key,),
                )

    # views
    def add_view(self, piece_key: str) -> None:
        with self._lock:
            self._pending[piece_key] = self._pending.get(piece_key, 0) + 1

    def _all_views(self) -> dict[str, int]:
        result = {
            k: c for k, c in self._conn.execute("SELECT piece_key, count FROM views")
        }
        for k, c in self._pending.items():
            result[k] = result.get(k, 0) + c
        return result

    def views(self) -> dict[str, int]:
        with self._lock:
            return self._all_views()

    def flush_views(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._pending:
            return
        with self._conn:
            self._conn.executemany(
                "INSERT INTO views(piece_key, count) VALUES(?, ?) "
                "ON CONFLICT(piece_key) DO UPDATE SET count=count+excluded.count",
                list(self._pending.items()),
            )
        self._pending.clear()

    # custom
    def add_custom_manufacturer(self, name: str) -> str:
        slug = _slug(name)
        if not slug:
            raise ValueError("manufacturer name has no usable characters")
        mid = f"c-{slug}"
        with self._lock, self._conn:
            if self._conn.execute(
                "SELECT 1 FROM custom_manufacturers WHERE id=?", (mid,)
            ).fetchone():
                raise ValueError(f"manufacturer {mid} already exists")
            self._conn.execute(
                "INSERT INTO custom_manufacturers(id, name) VALUES(?, ?)", (mid, name)
            )
        return mid

    def list_custom_manufacturers(self) -> list[CustomManufacturer]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name FROM custom_manufacturers ORDER BY rowid"
            ).fetchall()
        return [CustomManufacturer(*r) for r in rows]

    def add_custom_piece(
        self,
        manufacturer_id: str,
        name: str,
        category: str,
        video_file: str,
        image_file: str | None,
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO custom_pieces"
                "(manufacturer_id, name, category, video_file, image_file) "
                "VALUES(?, ?, ?, ?, ?)",
                (manufacturer_id, name, category, video_file, image_file),
            )
            return int(cur.lastrowid)

    def list_custom_pieces(self) -> list[CustomRow]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_PIECE_COLS} FROM custom_pieces ORDER BY id"
            ).fetchall()
        return [CustomRow(*r) for r in rows]

    def update_custom_piece(self, id: int, name: str, category: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE custom_pieces SET name=?, category=? WHERE id=?",
                (name, category, id),
            )

    def delete_custom_piece(self, id: int) -> CustomRow | None:
        with self._lock, self._conn:
            row = self._conn.execute(
                f"SELECT {_PIECE_COLS} FROM custom_pieces WHERE id=?", (id,)
            ).fetchone()
            if row is None:
                return None
            piece = CustomRow(*row)
            self._conn.execute("DELETE FROM custom_pieces WHERE id=?", (id,))
            left = self._conn.execute(
                "SELECT 1 FROM custom_pieces WHERE manufacturer_id=? LIMIT 1",
                (piece.manufacturer_id,),
            ).fetchone()
            if left is None:
                removed = self._conn.execute(
                    "DELETE FROM custom_manufacturers WHERE id=?",
                    (piece.manufacturer_id,),
                ).rowcount
                if removed:
                    self._conn.execute(
                        "DELETE FROM enabled_manufacturers WHERE id=?",
                        (piece.manufacturer_id,),
                    )
            return piece

    # backup
    def export_state(self) -> dict:
        with self._lock:
            settings = dict(self._conn.execute("SELECT key, value FROM settings"))
            enabled = sorted(
                r[0] for r in self._conn.execute("SELECT id FROM enabled_manufacturers")
            )
            disabled = sorted(
                r[0] for r in self._conn.execute("SELECT piece_key FROM disabled_pieces")
            )
            views = self._all_views()
        return {
            "settings": settings,
            "enabled_manufacturers": enabled,
            "disabled_pieces": disabled,
            "views": views,
        }

    def import_state(self, data: dict) -> None:
        with self._lock, self._conn:
            for table in ("settings", "enabled_manufacturers", "disabled_pieces", "views"):
                self._conn.execute(f"DELETE FROM {table}")
            self._conn.executemany(
                "INSERT INTO settings(key, value) VALUES(?, ?)",
                [(str(k), str(v)) for k, v in data.get("settings", {}).items()],
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO enabled_manufacturers(id) VALUES(?)",
                [(i,) for i in data.get("enabled_manufacturers", [])],
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO disabled_pieces(piece_key) VALUES(?)",
                [(i,) for i in data.get("disabled_pieces", [])],
            )
            self._conn.executemany(
                "INSERT INTO views(piece_key, count) VALUES(?, ?)",
                [(k, int(c)) for k, c in data.get("views", {}).items()],
            )
            self._pending.clear()

    def close(self) -> None:
        with self._lock:
            self._flush_locked()
            self._conn.close()
