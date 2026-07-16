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


def test_ocr_engine_default_oneocr():
    assert AppConfig().ocr_engine == "oneocr"


def test_from_dict_normalizes_legacy_ocr_and_stable():
    c = AppConfig.from_dict({"ocr_engine": "auto", "monitor_wait_stable": False, "monitor_stable_ms": 800})
    assert c.ocr_engine == "oneocr"
    assert c.monitor_stable_ms == 0
    assert c.monitor_wait_stable is False

    c2 = AppConfig.from_dict({"ocr_engine": "manga", "monitor_stable_ms": 1200})
    assert c2.ocr_engine == "manga"
    assert c2.monitor_stable_ms == 1200
    assert c2.monitor_wait_stable is True


def test_context_history_size_default():
    assert AppConfig().context_history_size == 3
    c = AppConfig(context_history_size=5)
    assert c.context_history_size == 5


def test_abs_region():
    c = AppConfig()
    assert not c.has_abs_region
    c.set_abs_region(10, 20, 300, 40)
    assert c.has_abs_region
    assert (c.region_abs_x, c.region_abs_y, c.region_abs_w, c.region_abs_h) == (
        10,
        20,
        300,
        40,
    )


def test_default_config_path_is_project_dir():
    path = default_config_path()
    assert path.name == "config.json"
    assert path.parent == project_root()
