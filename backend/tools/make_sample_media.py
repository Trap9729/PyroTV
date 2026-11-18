"""Generate sample fireworks media and the sample catalogue.

Usage: python tools/make_sample_media.py [OUT_DIR] [--catalogue PATH]

Writes OUT_DIR/videos/<name>.mp4 (10 s, 1080p30, H.264) and
OUT_DIR/images/<name>.jpg, plus catalogue/catalogue.json.
ffmpeg is resolved from $FFMPEG, then PATH, then imageio-ffmpeg.
"""
from __future__ import annotations

import argparse
import colorsys
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[2]

SAMPLE = {
    "version": 1,
    "manufacturers": [
        {
            "id": "lesli",
            "name": "Lesli",
            "categories": ["Tårtor", "Singelskott", "Raketer"],
            "pieces": [
                {"name": "Åska 49", "category": "Tårtor"},
                {"name": "Sprakande Örn", "category": "Tårtor"},
                {"name": "Ärta 25 skott", "category": "Tårtor"},
                {"name": "Blå Måne", "category": "Singelskott"},
                {"name": "Röd Drake", "category": "Singelskott"},
                {"name": "Himmelsstormare 3", "category": "Raketer"},
            ],
        },
        {
            "id": "nico",
            "name": "Nico",
            "categories": ["Tårtor", "Singelskott", "Raketer"],
            "pieces": [
                {"name": "Gyllene Regn 100", "category": "Tårtor"},
                {"name": "Älvdans", "category": "Tårtor"},
                {"name": "Öga för Öga 16", "category": "Singelskott"},
                {"name": "Vit Stjärna", "category": "Singelskott"},
                {"name": "Raket Åttan", "category": "Raketer"},
                {"name": "Sköna Maj 8", "category": "Raketer"},
            ],
        },
    ],
}


def find_ffmpeg() -> str:
    env = os.environ.get("FFMPEG")
    if env:
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as e:
        raise SystemExit("ffmpeg not found: set FFMPEG, put it on PATH, or pip install imageio-ffmpeg") from e


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _colour(index: int, total: int, dark: bool) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(index / total, 0.7, 0.35 if dark else 0.9)
    return int(r * 255), int(g * 255), int(b * 255)


def _draw_card(name: str, size: tuple[int, int], bg: tuple[int, int, int], font_px: int) -> Image.Image:
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    font = _font(font_px)
    # shrink to fit
    while d.textlength(name, font=font) > size[0] * 0.9 and font_px > 12:
        font_px -= 4
        font = _font(font_px)
    d.text((size[0] / 2, size[1] / 2), name, font=font, fill="white", anchor="mm")
    return img


def make_video(ffmpeg: str, name: str, bg: tuple[int, int, int], out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        frame = Path(tmp) / "frame.png"
        _draw_card(name, (1920, 1080), bg, 140).save(frame)
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-loop", "1", "-framerate", "30", "-i", str(frame),
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t", "10", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
            "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "32k",
            "-movflags", "+faststart", "-shortest", str(out),
        ]
        subprocess.run(cmd, check=True)


def make_image(name: str, bg: tuple[int, int, int], out: Path) -> None:
    _draw_card(name, (600, 800), bg, 64).save(out, "JPEG", quality=88)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out_dir", nargs="?", default="sample_data", type=Path)
    ap.add_argument("--catalogue", type=Path, default=REPO_ROOT / "catalogue" / "catalogue.json")
    args = ap.parse_args(argv)

    ffmpeg = find_ffmpeg()
    videos = args.out_dir / "videos"
    images = args.out_dir / "images"
    videos.mkdir(parents=True, exist_ok=True)
    images.mkdir(parents=True, exist_ok=True)

    names = [
        unicodedata.normalize("NFC", p["name"])
        for m in SAMPLE["manufacturers"]
        for p in m["pieces"]
    ]
    for i, name in enumerate(names):
        print(f"[{i + 1}/{len(names)}] {name}", flush=True)
        make_video(ffmpeg, name, _colour(i, len(names), dark=True), videos / f"{name}.mp4")
        make_image(name, _colour(i, len(names), dark=False), images / f"{name}.jpg")

    args.catalogue.parent.mkdir(parents=True, exist_ok=True)
    args.catalogue.write_text(
        json.dumps(SAMPLE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.catalogue}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
