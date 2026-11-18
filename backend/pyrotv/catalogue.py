from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

CATALOGUE_VERSION = 1
CUSTOM_ID_PREFIX = "c-"  # reserved for user-added manufacturers

_VIDEO_EXTS = (".mp4",)
_IMAGE_EXTS = (".jpg", ".jpeg", ".png")  # in order of preference


class CatalogueError(Exception):
    pass


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _fold(s: str) -> str:
    return _nfc(s).casefold()


def make_key(manufacturer_id: str, name: str) -> str:
    return f"{manufacturer_id}:{_nfc(name)}"


@dataclass(frozen=True)
class Manufacturer:
    id: str
    name: str
    categories: tuple[str, ...]


@dataclass(frozen=True)
class Piece:
    key: str
    name: str
    manufacturer_id: str
    category: str
    video: Path | None
    image: Path | None
    custom: bool = False


@dataclass(frozen=True)
class Catalogue:
    manufacturers: list[Manufacturer]
    pieces: list[Piece]
    missing: list[str]


def _index(directory: Path, exts: tuple[str, ...]) -> dict[str, Path]:
    """Map folded stem -> path; earlier entries in `exts` win."""
    best: dict[str, tuple[int, Path]] = {}
    if not directory.is_dir():
        return {}
    for p in sorted(directory.iterdir()):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext not in exts:
            continue
        rank = exts.index(ext)
        stem = _fold(p.stem)
        cur = best.get(stem)
        if cur is None or rank < cur[0]:
            best[stem] = (rank, p)
    return {k: v[1] for k, v in best.items()}


def _str(obj: object, field: str, ctx: str) -> str:
    if not isinstance(obj, dict) or not isinstance(obj.get(field), str) or not obj[field].strip():
        raise CatalogueError(f"{ctx}: missing or invalid '{field}'")
    return obj[field]


def load_catalogue(catalogue_path: Path, videos_dir: Path, images_dir: Path) -> Catalogue:
    try:
        raw = json.loads(Path(catalogue_path).read_text(encoding="utf-8"))
    except OSError as e:
        raise CatalogueError(f"cannot read catalogue: {e}") from e
    except ValueError as e:
        raise CatalogueError(f"invalid catalogue JSON: {e}") from e
    if not isinstance(raw, dict):
        raise CatalogueError("catalogue root must be an object")
    if raw.get("version") != CATALOGUE_VERSION:
        raise CatalogueError(f"unsupported catalogue version: {raw.get('version')!r}")
    raw_mans = raw.get("manufacturers")
    if not isinstance(raw_mans, list):
        raise CatalogueError("'manufacturers' must be a list")

    videos = _index(videos_dir, _VIDEO_EXTS)
    images = _index(images_dir, _IMAGE_EXTS)

    manufacturers: list[Manufacturer] = []
    pieces: list[Piece] = []
    missing: list[str] = []
    seen_ids: set[str] = set()

    for m in raw_mans:
        mid = _str(m, "id", "manufacturer")
        if mid.startswith(CUSTOM_ID_PREFIX):
            raise CatalogueError(f"manufacturer id {mid!r} uses reserved prefix {CUSTOM_ID_PREFIX!r}")
        if mid in seen_ids:
            raise CatalogueError(f"duplicate manufacturer id {mid!r}")
        seen_ids.add(mid)
        mname = _str(m, "name", f"manufacturer {mid}")
        cats = m.get("categories")
        if not isinstance(cats, list) or not all(isinstance(c, str) for c in cats):
            raise CatalogueError(f"manufacturer {mid}: 'categories' must be a list of strings")
        raw_pieces = m.get("pieces")
        if not isinstance(raw_pieces, list):
            raise CatalogueError(f"manufacturer {mid}: 'pieces' must be a list")
        manufacturers.append(Manufacturer(mid, mname, tuple(cats)))

        seen_names: set[str] = set()
        for rp in raw_pieces:
            name = _nfc(_str(rp, "name", f"manufacturer {mid} piece"))
            category = _str(rp, "category", f"piece {name!r}")
            if category not in cats:
                raise CatalogueError(
                    f"piece {name!r}: category {category!r} not in manufacturer {mid} categories"
                )
            folded = _fold(name)
            if folded in seen_names:
                raise CatalogueError(f"duplicate piece name {name!r} in manufacturer {mid}")
            seen_names.add(folded)
            video = videos.get(folded)
            if video is None:
                missing.append(name)
                continue
            pieces.append(
                Piece(
                    key=make_key(mid, name),
                    name=name,
                    manufacturer_id=mid,
                    category=category,
                    video=video,
                    image=images.get(folded),
                )
            )
    return Catalogue(manufacturers, pieces, missing)
