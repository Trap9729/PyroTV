from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from pyrotv import media
from pyrotv.media import VideoInfo, build_ffmpeg_args, needs_transcode, probe, process_image, process_video

OK = VideoInfo("h264", 1920, 1080, 30.0, True)


def test_needs_transcode():
    assert not needs_transcode(OK)
    assert not needs_transcode(VideoInfo("h264", 1280, 720, 30000 / 1001, True))
    assert needs_transcode(VideoInfo("hevc", 1920, 1080, 30.0, True))
    assert needs_transcode(VideoInfo("h264", 3840, 2160, 30.0, True))
    assert needs_transcode(VideoInfo("h264", 1920, 1080, 60.0, True))
    assert not needs_transcode(VideoInfo("h264", 1920, 1080, 30.0, True, "yuvj420p"))
    assert needs_transcode(VideoInfo("h264", 1920, 1080, 30.0, True, "yuv420p10le"))
    assert needs_transcode(VideoInfo("h264", 1920, 1080, 30.0, True, "yuv444p"))


def test_args_copy():
    a = build_ffmpeg_args(Path("in.mp4"), Path("out.part"), OK, ffmpeg="ffmpeg")
    assert a == ["ffmpeg", "-y", "-i", "in.mp4", "-map", "0:v:0", "-map", "0:a:0?",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                 "-f", "mp4", "out.part"]


def test_args_transcode():
    a = build_ffmpeg_args(Path("in.mov"), Path("out.part"), VideoInfo("hevc", 3840, 2160, 60.0, True),
                          ffmpeg="ffmpeg")
    j = " ".join(a)
    for want in ("libx264", "-pix_fmt yuv420p", "aac", "-r 30", "-f mp4", "+faststart", "-map 0:a:0?",
                 "-threads 2"):
        assert want in j
    assert "scale=-2:'min(1080,ih)'" in a
    assert a[-1] == "out.part"


def test_probe_parses_json():
    out = ('{"streams":[{"codec_type":"audio"},{"codec_type":"video","codec_name":"h264",'
           '"width":1920,"height":1080,"r_frame_rate":"30000/1001","pix_fmt":"yuv420p10le"}]}')

    def run(cmd, **kw):
        assert cmd[0] == "ffprobe" and cmd[-1] == "f.mp4"
        assert kw.get("timeout") == 60
        return subprocess.CompletedProcess(cmd, 0, out, "")

    info = probe(Path("f.mp4"), run=run, ffprobe="ffprobe")
    assert info == VideoInfo("h264", 1920, 1080, pytest.approx(29.97, abs=0.01), True, "yuv420p10le")
    assert needs_transcode(info)


def test_probe_no_video():
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, '{"streams":[{"codec_type":"audio"}]}', "")

    with pytest.raises(ValueError):
        probe(Path("f.mp4"), run=run, ffprobe="ffprobe")


def test_process_image(tmp_path):
    src = tmp_path / "big.png"
    Image.new("RGB", (4000, 3000), (10, 200, 10)).save(src)
    dest = tmp_path / "out.jpg"
    process_image(src, dest)
    with Image.open(dest) as im:
        assert im.format == "JPEG" and max(im.size) == 1600
    assert not (tmp_path / "out.jpg.part").exists()


def test_process_image_large_jpeg_uses_draft(tmp_path, monkeypatch):
    src = tmp_path / "big.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees: exif_transpose must still apply after draft()
    Image.new("RGB", (7000, 5000), (200, 10, 10)).save(src, exif=exif.tobytes())
    drafts = []
    real_draft = Image.Image.draft

    def spy(self, mode, size):
        drafts.append(size)
        return real_draft(self, mode, size)

    monkeypatch.setattr(Image.Image, "draft", spy)
    dest = tmp_path / "out.jpg"
    process_image(src, dest)
    assert drafts == [(3200, 3200)]
    with Image.open(dest) as im:
        assert im.size == (1143, 1600)


def test_process_image_no_upscale_and_failure(tmp_path):
    src = tmp_path / "s.png"
    Image.new("RGBA", (200, 100)).save(src)
    dest = tmp_path / "o.jpg"
    process_image(src, dest)
    with Image.open(dest) as im:
        assert im.size == (200, 100)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    dest2 = tmp_path / "o2.jpg"
    with pytest.raises(Exception):
        process_image(bad, dest2)
    assert not dest2.exists() and not (tmp_path / "o2.jpg.part").exists()


def test_process_video_failure_cleans_up(tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    dest = tmp_path / "out.mp4"

    def run(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"partial")
        return subprocess.CompletedProcess(cmd, 1, "", "boom " * 200)

    with pytest.raises(RuntimeError) as e:
        process_video(src, dest, info=OK, run=run, ffmpeg="ffmpeg")
    assert len(str(e.value)) <= 600 and "boom" in str(e.value)
    assert not dest.exists() and not (tmp_path / "out.mp4.part").exists()


def test_process_video_success_with_stub(tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    dest = tmp_path / "out.mp4"

    def run(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"done")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    process_video(src, dest, info=OK, run=run, ffmpeg="ffmpeg")
    assert dest.read_bytes() == b"done" and not (tmp_path / "out.mp4.part").exists()


@pytest.mark.parametrize("info,timeout", [(OK, 1800), (VideoInfo("hevc", 1920, 1080, 30.0, True), 7200)])
def test_process_video_passes_timeout(tmp_path, info, timeout):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    seen = {}

    def run(cmd, **kw):
        seen.update(kw)
        Path(cmd[-1]).write_bytes(b"done")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    process_video(src, tmp_path / "out.mp4", info=info, run=run, ffmpeg="ffmpeg")
    assert seen["timeout"] == timeout


def test_process_video_timeout_cleans_up(tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    dest = tmp_path / "out.mp4"

    def run(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"partial")
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    with pytest.raises(RuntimeError, match="ffmpeg timed out"):
        process_video(src, dest, info=OK, run=run, ffmpeg="ffmpeg")
    assert not dest.exists() and not (tmp_path / "out.mp4.part").exists()


def test_process_video_nice_prefix(tmp_path, monkeypatch):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        with open(cmd[-1], "wb") as f:  # no Path(): os.name is patched to "posix"
            f.write(b"done")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(media.os, "name", "posix")
    monkeypatch.setattr(media.shutil, "which", lambda name: "/usr/bin/nice" if name == "nice" else None)
    process_video(src, tmp_path / "out.mp4", info=OK, run=run, ffmpeg="ffmpeg")
    assert seen[0][:4] == ["nice", "-n", "10", "ffmpeg"]


def _ffmpeg_or_none():
    try:
        return media.ffmpeg_exe()
    except RuntimeError:
        return None


@pytest.mark.skipif(_ffmpeg_or_none() is None, reason="no ffmpeg binary available")
def test_process_video_real_transcode(tmp_path):
    ff = media.ffmpeg_exe()
    src = tmp_path / "src.mp4"
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=1",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    dest = tmp_path / "dest.mp4"
    process_video(src, dest, info=VideoInfo("hevc", 3840, 2160, 60.0, False))
    assert dest.is_file() and not (tmp_path / "dest.mp4.part").exists()
    r = subprocess.run([ff, "-v", "error", "-i", str(dest), "-f", "null", "-"])
    assert r.returncode == 0
