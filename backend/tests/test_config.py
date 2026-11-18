from pyrotv.config import load_config


def test_load_config_defaults(tmp_path):
    cfg = load_config({"PYROTV_DATA_DIR": str(tmp_path)})
    assert cfg.db_path == tmp_path / "pyrotv.db"
    assert cfg.tv_connector == "HDMI-A-2"
    assert cfg.fake_hw is False
    assert cfg.mpv_args == []
    assert cfg.sink_hdmi == "" and cfg.sink_jack == ""


def test_load_config_env_overrides(tmp_path):
    cfg = load_config({
        "PYROTV_DATA_DIR": str(tmp_path),
        "PYROTV_CATALOGUE": str(tmp_path / "c.json"),
        "PYROTV_STATIC": str(tmp_path / "s"),
        "PYROTV_TV_CONNECTOR": "HDMI-A-1",
        "PYROTV_FAKE_HW": "1",
        "PYROTV_SINK_HDMI": "hdmi",
        "PYROTV_SINK_JACK": "jack",
        "PYROTV_MPV_ARGS": "--foo=1 --bar 'a b'",
    })
    assert cfg.catalogue_path == tmp_path / "c.json"
    assert cfg.static_dir == tmp_path / "s"
    assert cfg.tv_connector == "HDMI-A-1"
    assert cfg.fake_hw is True
    assert cfg.sink_hdmi == "hdmi" and cfg.sink_jack == "jack"
    assert cfg.mpv_args == ["--foo=1", "--bar", "a b"]


def test_default_paths_relative_to_repo():
    cfg = load_config({})
    assert cfg.static_dir.name == "frontend"
    assert cfg.catalogue_path.name == "catalogue.json"
    assert cfg.catalogue_path.parent.name == "catalogue"


def test_ensure_dirs_creates_everything(tmp_path):
    cfg = load_config({"PYROTV_DATA_DIR": str(tmp_path)})
    cfg.ensure_dirs()
    for d in (cfg.media_videos, cfg.media_images, cfg.custom_videos,
              cfg.custom_images, cfg.thumbs_dir, cfg.upload_tmp_dir):
        assert d.is_dir()
    assert cfg.hotspot_file == tmp_path / "hotspot.json"
    assert cfg.mpv_socket == tmp_path / "mpv.sock"
    assert cfg.media_videos == tmp_path / "media" / "videos"
    assert cfg.thumbs_dir == tmp_path / "cache" / "thumbs"
    assert cfg.upload_tmp_dir == tmp_path / "tmp" / "uploads"
