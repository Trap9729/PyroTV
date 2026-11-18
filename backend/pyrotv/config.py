from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Config:
    data_dir: Path
    catalogue_path: Path
    static_dir: Path
    tv_connector: str = "HDMI-A-2"
    fake_hw: bool = False
    sink_hdmi: str = ""
    sink_jack: str = ""
    mpv_args: list[str] = field(default_factory=list)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "pyrotv.db"

    @property
    def media_videos(self) -> Path:
        return self.data_dir / "media" / "videos"

    @property
    def media_images(self) -> Path:
        return self.data_dir / "media" / "images"

    @property
    def custom_videos(self) -> Path:
        return self.data_dir / "custom" / "videos"

    @property
    def custom_images(self) -> Path:
        return self.data_dir / "custom" / "images"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "cache" / "thumbs"

    @property
    def upload_tmp_dir(self) -> Path:
        return self.data_dir / "tmp" / "uploads"

    @property
    def hotspot_file(self) -> Path:
        return self.data_dir / "hotspot.json"

    @property
    def mpv_socket(self) -> Path:
        return self.data_dir / "mpv.sock"

    def ensure_dirs(self) -> None:
        for d in (
            self.data_dir,
            self.media_videos,
            self.media_images,
            self.custom_videos,
            self.custom_images,
            self.thumbs_dir,
            self.upload_tmp_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


def load_config(env: Mapping[str, str] = os.environ) -> Config:
    return Config(
        data_dir=Path(env.get("PYROTV_DATA_DIR", "/data")),
        catalogue_path=Path(
            env.get("PYROTV_CATALOGUE", str(REPO_ROOT / "catalogue" / "catalogue.json"))
        ),
        static_dir=Path(env.get("PYROTV_STATIC", str(REPO_ROOT / "frontend"))),
        tv_connector=env.get("PYROTV_TV_CONNECTOR", "HDMI-A-2"),
        fake_hw=env.get("PYROTV_FAKE_HW") == "1",
        sink_hdmi=env.get("PYROTV_SINK_HDMI", ""),
        sink_jack=env.get("PYROTV_SINK_JACK", ""),
        mpv_args=shlex.split(env.get("PYROTV_MPV_ARGS", "")),
    )
