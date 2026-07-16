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


def test_pipeline_mode_and_optional_llm_defaults():
    c = AppConfig()
    assert c.pipeline_mode == "ocr"
    assert c.temperature is None
    assert c.top_p is None
    assert c.top_k is None
    assert c.reasoning_effort == ""
    assert c.ui_language == "zh-Hant"
    assert c.obs_enabled is False
    assert c.obs_port == 8765


def test_from_dict_pipeline_and_sampling():
    c = AppConfig.from_dict(
        {
            "pipeline_mode": "vlm",
            "temperature": 0.2,
            "top_k": 0,
            "reasoning_effort": "HIGH",
            "ui_language": "en",
            "obs_port": 9000,
        }
    )
    assert c.pipeline_mode == "vlm"
    assert c.temperature == 0.2
    assert c.top_k is None  # 0 normalized to unset
    assert c.reasoning_effort == "high"
    assert c.ui_language == "en"
    assert c.obs_port == 9000

    c2 = AppConfig.from_dict({"pipeline_mode": "nope", "ui_language": "ja"})
    assert c2.pipeline_mode == "ocr"
    assert c2.ui_language == "zh-Hant"


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


def test_overlay_obs_style_defaults():
    c = AppConfig()
    assert c.overlay_show_source is True
    assert c.overlay_show_translation is True
    assert c.overlay_source_font_size == 14
    assert c.overlay_translation_font_size == 16
    assert c.obs_show_translation is True
    assert c.obs_translation_font_size == 28


def test_legacy_font_size_migration():
    c = AppConfig.from_dict({"overlay_font_size": 20, "obs_font_size": 40})
    assert c.overlay_translation_font_size == 20
    assert c.overlay_source_font_size == 18
    assert c.overlay_font_size == 20
    assert c.obs_translation_font_size == 40
    assert c.obs_source_font_size == max(10, int(round(40 * 0.72)))
    assert c.obs_font_size == 40


def test_style_color_and_align_normalize():
    from app.config import normalize_hex_color

    assert normalize_hex_color("#abc", "#ffffff") == "#aabbcc"
    assert normalize_hex_color("ff0000", "#ffffff") == "#ff0000"
    assert normalize_hex_color("nope", "#c8c8d8") == "#c8c8d8"

    c = AppConfig.from_dict(
        {
            "overlay_source_color": "fff",
            "overlay_text_align": "CENTER",
            "overlay_show_source": False,
            "overlay_show_translation": False,
            "obs_bg_alpha": 999,
            "overlay_bg_alpha": 0,
        }
    )
    assert c.overlay_source_color == "#ffffff"
    assert c.overlay_text_align == "center"
    assert c.overlay_show_translation is True  # forced at least one
    assert c.obs_bg_alpha == 255
    assert c.overlay_bg_alpha == 0  # zero is valid (fully transparent)


def test_from_dict_bad_sampling_does_not_drop_config():
    c = AppConfig.from_dict(
        {
            "api_key": "sk-test",
            "model": "keep-me",
            "top_k": "abc",
            "temperature": "nope",
            "region_w": 100,
            "region_h": 40,
        }
    )
    assert c.api_key == "sk-test"
    assert c.model == "keep-me"
    assert c.top_k is None
    assert c.temperature is None
    assert c.region_w == 100
