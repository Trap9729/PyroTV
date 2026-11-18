"""The zero-argument factory used by the systemd unit (`uvicorn pyrotv.main:app --factory`)."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from pyrotv import main


def test_app_factory_builds_from_env(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "data"
    catalogue = tmp_path / "catalogue.json"
    catalogue.write_text(
        json.dumps({"version": 1, "manufacturers": []}), encoding="utf-8"
    )
    monkeypatch.setenv("PYROTV_DATA_DIR", str(data))
    monkeypatch.setenv("PYROTV_CATALOGUE", str(catalogue))
    monkeypatch.setenv("PYROTV_STATIC", str(tmp_path / "no-static"))
    monkeypatch.setenv("PYROTV_FAKE_HW", "1")

    app = main.app()

    with TestClient(app, client=("127.0.0.1", 5000)) as client:
        r = client.get("/api/state")
        assert r.status_code == 200
        assert r.json()["setup_done"] is False
    # The lifespan created the data dir layout from PYROTV_DATA_DIR.
    assert (data / "pyrotv.db").is_file()
    assert (data / "media" / "videos").is_dir()
