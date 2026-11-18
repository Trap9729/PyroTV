from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pyrotv.app import create_app
from pyrotv.config import Config

TOUCH = ("127.0.0.1", 5000)
PHONE = ("10.42.0.7", 5000)
OUTSIDER = ("192.168.1.5", 5000)

KEY_ASKA = "lesli:Åska 49"
KEY_BLIXT = "lesli:Blixt"
KEY_ORN = "pyro:Örn"

VIDEO_BYTES = bytes(range(256)) * 4  # 1024 bytes


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    data = tmp_path / "data"
    videos = data / "media" / "videos"
    images = data / "media" / "images"
    videos.mkdir(parents=True)
    images.mkdir(parents=True)
    catalogue = {
        "version": 1,
        "manufacturers": [
            {
                "id": "lesli",
                "name": "Lesli Fyrverkerier",
                "categories": ["Batterier", "Åska"],
                "pieces": [
                    {"name": "Åska 49", "category": "Åska"},
                    {"name": "Blixt", "category": "Batterier"},
                ],
            },
            {
                "id": "pyro",
                "name": "Ö-Pyro",
                "categories": ["Batterier"],
                "pieces": [{"name": "Örn", "category": "Batterier"}],
            },
        ],
    }
    cat_path = tmp_path / "catalogue.json"
    cat_path.write_text(json.dumps(catalogue), encoding="utf-8")
    for name in ("Åska 49", "Blixt", "Örn"):
        (videos / f"{name}.mp4").write_bytes(VIDEO_BYTES)
    Image.new("RGB", (1200, 800), (200, 30, 30)).save(images / "Åska 49.jpg")
    Image.new("RGB", (600, 400), (30, 30, 200)).save(images / "Örn.jpg")
    return Config(
        data_dir=data,
        catalogue_path=cat_path,
        static_dir=tmp_path / "no-static",
        fake_hw=True,
    )


@pytest.fixture
def make_client(cfg: Config):
    """Context manager yielding a TestClient (lifespan running) for a client host.

    TestClient's default host is "testclient", which the allow-list denies, so
    always pass an explicit (host, port).
    """

    @contextmanager
    def _make(client=TOUCH, *, tv: bool = False, **kwargs):
        if tv:
            cfg.data_dir.mkdir(parents=True, exist_ok=True)
            (cfg.data_dir / "fake_tv").write_text("1")
        app = create_app(cfg, watcher_interval=0.05, **kwargs)
        with TestClient(app, client=client) as c:
            if tv:
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if c.get("/api/state").json()["tv_connected"]:
                        break
                    time.sleep(0.02)
                else:
                    raise AssertionError("tv never connected")
            c.app_state = app.state
            yield c

    return _make
