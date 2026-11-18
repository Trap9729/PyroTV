from __future__ import annotations

import threading
import unicodedata
from dataclasses import dataclass, replace
from typing import Literal

from .catalogue import Catalogue, Manufacturer, Piece, make_key
from .config import Config
from .store import Store

_SV_MAP = str.maketrans({"å": "{1", "ä": "{2", "ö": "{3"})


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def sv_sort_key(s: str) -> str:
    """Swedish collation key: å, ä, ö sort after z."""
    return _nfc(s).casefold().translate(_SV_MAP)


@dataclass(frozen=True)
class PieceView:
    key: str
    name: str
    manufacturer_id: str
    category: str
    has_image: bool
    custom: bool
    views: int


class Library:
    def __init__(self, cfg: Config, catalogue: Catalogue, store: Store) -> None:
        self._cfg = cfg
        self._catalogue = catalogue
        self._store = store
        self._lock = threading.Lock()
        self._custom_pieces: tuple[Piece, ...] = ()
        self._custom_mans: tuple[Manufacturer, ...] = ()
        self.reload_custom()

    def reload_custom(self) -> None:
        cfg = self._cfg
        rows = self._store.list_custom_pieces()
        pieces: list[Piece] = []
        for row in rows:
            video = cfg.custom_videos / row.video_file
            if not video.is_file():
                continue
            image = cfg.custom_images / row.image_file if row.image_file else None
            if image is not None and not image.is_file():
                image = None
            pieces.append(
                Piece(
                    key=make_key(row.manufacturer_id, row.name),
                    name=_nfc(row.name),
                    manufacturer_id=row.manufacturer_id,
                    category=row.category,
                    video=video,
                    image=image,
                    custom=True,
                )
            )
        mans: list[Manufacturer] = []
        for cm in self._store.list_custom_manufacturers():
            cats = {p.category for p in pieces if p.manufacturer_id == cm.id}
            mans.append(Manufacturer(cm.id, cm.name, tuple(sorted(cats, key=sv_sort_key))))
        with self._lock:
            self._custom_pieces = tuple(pieces)
            self._custom_mans = tuple(mans)

    def _snapshot(self) -> tuple[tuple[Piece, ...], tuple[Manufacturer, ...]]:
        with self._lock:
            return self._custom_pieces, self._custom_mans

    def _all_pieces(self) -> list[Piece]:
        custom, _ = self._snapshot()
        return [*self._catalogue.pieces, *custom]

    def list(
        self,
        *,
        sort: Literal["name", "views"] = "name",
        category: str | None = None,
        query: str | None = None,
        include_disabled: bool = False,
    ) -> list[PieceView]:
        pieces = self._all_pieces()
        if not include_disabled:
            enabled = self._store.enabled_manufacturers()
            disabled = self._store.disabled_pieces()
            pieces = [
                p for p in pieces if p.manufacturer_id in enabled and p.key not in disabled
            ]
        if category is not None:
            cat = _nfc(category)
            pieces = [p for p in pieces if _nfc(p.category) == cat]
        q = _nfc(query).casefold().strip() if query else ""
        if q:
            pieces = [p for p in pieces if q in _nfc(p.name).casefold()]
        views = self._store.views()
        out = [
            PieceView(
                key=p.key,
                name=p.name,
                manufacturer_id=p.manufacturer_id,
                category=p.category,
                has_image=p.image is not None,
                custom=p.custom,
                views=views.get(p.key, 0),
            )
            for p in pieces
        ]
        if sort == "views":
            out.sort(key=lambda v: (-v.views, sv_sort_key(v.name), v.key))
        else:
            out.sort(key=lambda v: (sv_sort_key(v.name), v.key))
        return out

    def categories(self) -> list[str]:
        return sorted({v.category for v in self.list()}, key=sv_sort_key)

    def get(self, key: str) -> Piece | None:
        for p in self._all_pieces():
            if p.key == key:
                return p
        return None

    def manufacturers(self) -> list[Manufacturer]:
        custom, custom_mans = self._snapshot()
        extra: dict[str, set[str]] = {}
        for p in custom:
            extra.setdefault(p.manufacturer_id, set()).add(p.category)
        result: list[Manufacturer] = []
        for m in self._catalogue.manufacturers:
            new = sorted(extra.get(m.id, set()) - set(m.categories), key=sv_sort_key)
            result.append(replace(m, categories=m.categories + tuple(new)) if new else m)
        result.extend(custom_mans)
        return result
