from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps


@dataclass(frozen=True)
class VideoInfo:
    codec: str
    width: int
    height: int
    fps: float
    has_audio: bool


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


Runner = Callable[..., "subprocess.CompletedProcess"]


def ffmpeg_exe() -> str:
    env = os.environ.get("FFMPEG")
    if env:
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    raise RuntimeError("ffmpeg not found")


def ffprobe_exe() -> str:
    env = os.environ.get("FFPROBE")
    if env:
        return env
    found = shutil.which("ffprobe")
    if found:
        return found
    raise RuntimeError("ffprobe not found")


def _fps(rate: str) -> float:
    try:
        num, _, den = rate.partition("/")
        d = float(den) if den else 1.0
        return float(num) / d if d else 0.0
    except ValueError:
        return 0.0


def probe(path: Path, *, run: Runner = _run, ffprobe: str | None = None) -> VideoInfo:
    exe = ffprobe or ffprobe_exe()
    res = run(
        [exe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]
    )
    if res.returncode != 0:
        raise ValueError(f"ffprobe failed: {(res.stderr or '')[-200:]}")
    streams = json.loads(res.stdout).get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("no video stream")
    return VideoInfo(
        codec=str(video.get("codec_name", "")),
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        fps=_fps(str(video.get("r_frame_rate", "0/1"))),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def needs_transcode(info: VideoInfo) -> bool:
    return not (info.codec == "h264" and info.height <= 1080 and info.fps <= 30.5)


def build_ffmpeg_args(src: Path, dest: Path, info: VideoInfo, *, ffmpeg: str | None = None) -> list[str]:
    exe = ffmpeg or ffmpeg_exe()
    if not needs_transcode(info):
        return [exe, "-y", "-i", str(src), "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(dest)]
    return [
        exe, "-y", "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", "scale=-2:'min(1080,ih)'",
        "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart", "-f", "mp4",
        str(dest),
    ]


def _fsync_file(path: Path) -> None:
    with open(path, "rb+") as f:
        os.fsync(f.fileno())


def process_video(
    src: Path,
    dest: Path,
    *,
    info: VideoInfo | None = None,
    run: Runner = _run,
    ffmpeg: str | None = None,
) -> None:
    part = dest.with_name(dest.name + ".part")
    try:
        if info is None:
            info = probe(src, run=run)
        res = run(build_ffmpeg_args(src, part, info, ffmpeg=ffmpeg))
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {(res.stderr or '')[-500:]}")
        _fsync_file(part)
        os.replace(part, dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def process_image(src: Path, dest: Path) -> None:
    part = dest.with_name(dest.name + ".part")
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((1600, 1600), Image.Resampling.LANCZOS)  # never upscales
            with open(part, "wb") as f:
                im.save(f, format="JPEG", quality=85)
                f.flush()
                os.fsync(f.fileno())
        os.replace(part, dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
