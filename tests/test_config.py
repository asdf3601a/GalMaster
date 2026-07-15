from pathlib import Path

from app.config import (
    AppConfig,
    default_config_path,
    load_config,
    project_root,
    save_config,
)


def test_save_load_roundtrip(tmp_path: Path):
    path = tmp_path / "cfg.json"
    cfg = AppConfig(api_key="secret", region_w=100, region_h=40, target_lang="en")
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.region_w == 100
    assert loaded.region_h == 40
    assert loaded.target_lang == "en"
    assert loaded.api_key == "secret"


def test_has_region():
    assert not AppConfig().has_region
    c = AppConfig(region_w=10, region_h=10)
    assert c.has_region


def test_has_llm():
    assert not AppConfig().has_llm
    assert AppConfig(api_key="  sk  ").has_llm
    assert not AppConfig(api_key="   ").has_llm


def test_monitor_wait_stable_default():
    assert AppConfig().monitor_wait_stable is True
    c = AppConfig(monitor_wait_stable=False)
    assert c.monitor_wait_stable is False


def test_monitor_stable_ms_default():
    assert AppConfig().monitor_stable_ms == 800
    c = AppConfig(monitor_stable_ms=1500)
    assert c.monitor_stable_ms == 1500


def test_context_history_size_default():
    assert AppConfig().context_history_size == 3
    c = AppConfig(context_history_size=5)
    assert c.context_history_size == 5


def test_default_config_path_is_project_dir():
    path = default_config_path()
    assert path.name == "config.json"
    assert path.parent == project_root()
