from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from .store import Store

BACKUP_NAME = "pyrotv-backup.json"
BACKUP_VERSION = 1


def export_backup(store: Store, dest_dir: Path) -> Path:
    """Write the settings backup atomically: a partial file never has the final name."""
    final = Path(dest_dir) / BACKUP_NAME
    part = Path(dest_dir) / (BACKUP_NAME + ".part")
    data = json.dumps(
        {"version": BACKUP_VERSION, "state": store.export_state()}, ensure_ascii=False, indent=1
    )
    try:
        with open(part, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(part, final)
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise
    return final


def import_backup(store: Store, path: Path) -> None:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"not a valid backup file: {e}") from None
    if not isinstance(doc, dict) or doc.get("version") != BACKUP_VERSION:
        raise ValueError("unsupported backup version")
    state = doc.get("state")
    if not isinstance(state, dict):
        raise ValueError("backup has no state")
    for key, kind in (
        ("settings", dict),
        ("views", dict),
        ("enabled_manufacturers", list),
        ("disabled_pieces", list),
    ):
        if key in state and not isinstance(state[key], kind):
            raise ValueError(f"backup field {key!r} has the wrong shape")
    try:
        store.import_state(state)
    except (TypeError, AttributeError, ValueError) as e:
        raise ValueError(f"backup content invalid: {e}") from None


def find_usb_mount(
    root: Path = Path("/media"), is_mount: Callable[[Path], bool] = os.path.ismount
) -> Path | None:
    """First real mount point at root/*/* (udisks layout) or root/*; None if none."""
    root = Path(root)
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return None
    for user_dir in children:
        try:
            subs = sorted(p for p in user_dir.iterdir() if p.is_dir())
        except OSError:
            continue
        for sub in subs:
            if is_mount(sub):
                return sub
    for child in children:
        if is_mount(child):
            return child
    return None
