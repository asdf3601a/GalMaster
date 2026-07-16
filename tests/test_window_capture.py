"""Window capture order: WGC → GDI → screen (OBS Automatic)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from app.capture import screenshot
from app.config import AppConfig


def _solid(w: int = 40, h: int = 20, color=(40, 80, 120)) -> Image.Image:
    """Near-uniform image (treated as blank by is_mostly_blank)."""
    return Image.new("RGB", (w, h), color)


def _content(w: int = 80, h: int = 40, seed: int = 0) -> Image.Image:
    """Non-blank image with enough variance for OCR-path acceptance."""
    rng = np.random.default_rng(seed)
    arr = rng.integers(20, 230, size=(h, w, 3), dtype=np.uint8)
    return Image.fromarray(arr, mode="RGB")


def _blank(w: int = 40, h: int = 20) -> Image.Image:
    return Image.new("RGB", (w, h), (0, 0, 0))


@pytest.fixture
def valid_hwnd():
    with patch("app.capture.windows.is_window_valid", return_value=True):
        yield 12345


def test_no_hwnd_uses_screen_only():
    screen = _content(40, 20)
    with (
        patch.object(screenshot, "capture_screen_region", return_value=screen) as scr,
        patch.object(screenshot, "capture_hwnd_client") as gdi,
        patch("app.capture.wgc.capture_hwnd_wgc") as wgc,
    ):
        out = screenshot.capture_region(
            hwnd=None, rel_x=10, rel_y=20, rel_w=40, rel_h=20
        )
        assert out is screen
        scr.assert_called_once_with(10, 20, 40, 20)
        gdi.assert_not_called()
        wgc.assert_not_called()


def test_auto_tries_wgc_then_gdi_then_screen(valid_hwnd):
    screen = _content(40, 20, seed=1)
    order: list[str] = []

    def fake_wgc(hwnd, **_k):
        order.append("wgc")
        return None  # force fallback

    def fake_gdi(hwnd):
        order.append("gdi")
        return None

    def fake_screen(*_a, **_k):
        order.append("screen")
        return screen

    with (
        patch("app.capture.wgc.capture_hwnd_wgc", side_effect=fake_wgc),
        patch.object(screenshot, "capture_hwnd_client", side_effect=fake_gdi),
        patch.object(screenshot, "capture_screen_region", side_effect=fake_screen),
        patch(
            "app.capture.windows.client_to_screen_rect",
            return_value=(1, 2, 40, 20),
        ),
    ):
        out = screenshot.capture_region(
            hwnd=valid_hwnd,
            rel_x=0,
            rel_y=0,
            rel_w=40,
            rel_h=20,
            method="auto",
        )
        assert out is screen
        assert order == ["wgc", "gdi", "screen"]


def test_bitblt_skips_wgc(valid_hwnd):
    gdi_client = _content(80, 40, seed=2)
    order: list[str] = []

    def fake_wgc(*_a, **_k):
        order.append("wgc")
        return _content(200, 100, seed=3)

    def fake_gdi(hwnd):
        order.append("gdi")
        return gdi_client

    with (
        patch("app.capture.wgc.capture_hwnd_wgc", side_effect=fake_wgc),
        patch.object(screenshot, "capture_hwnd_client", side_effect=fake_gdi),
        patch.object(screenshot, "capture_screen_region") as scr,
    ):
        out = screenshot.capture_region(
            hwnd=valid_hwnd,
            rel_x=0,
            rel_y=0,
            rel_w=40,
            rel_h=20,
            method="bitblt",
        )
        assert "wgc" not in order
        assert order[0] == "gdi"
        assert out.size == (40, 20)
        scr.assert_not_called()


def test_gdi_success_skips_screen(valid_hwnd):
    gdi_client = _content(100, 50, seed=4)

    with (
        patch("app.capture.wgc.capture_hwnd_wgc", return_value=None),
        patch.object(screenshot, "capture_hwnd_client", return_value=gdi_client),
        patch.object(screenshot, "capture_screen_region") as scr,
    ):
        out = screenshot.capture_region(
            hwnd=valid_hwnd,
            rel_x=5,
            rel_y=5,
            rel_w=40,
            rel_h=20,
            method="auto",
        )
        assert out.size == (40, 20)
        scr.assert_not_called()


def test_prefer_screen_legacy_skips_window_apis(valid_hwnd):
    screen = _content(40, 20, seed=5)
    with (
        patch("app.capture.wgc.capture_hwnd_wgc") as wgc,
        patch.object(screenshot, "capture_hwnd_client") as gdi,
        patch.object(screenshot, "capture_screen_region", return_value=screen) as scr,
        patch(
            "app.capture.windows.client_to_screen_rect",
            return_value=(9, 8, 40, 20),
        ),
    ):
        out = screenshot.capture_region(
            hwnd=valid_hwnd,
            rel_x=0,
            rel_y=0,
            rel_w=40,
            rel_h=20,
            prefer_screen=True,
        )
        assert out is screen
        wgc.assert_not_called()
        gdi.assert_not_called()
        scr.assert_called_once()


def test_capture_from_config_passes_method():
    cfg = AppConfig(
        bound_hwnd=1,
        region_x=0,
        region_y=0,
        region_w=10,
        region_h=10,
        window_capture_method="bitblt",
    )
    with patch.object(
        screenshot, "capture_region", return_value=_content(10, 10)
    ) as cap:
        screenshot.capture_from_config(cfg)
        assert cap.call_args.kwargs.get("method") == "bitblt"


def test_window_capture_method_config_normalize():
    assert AppConfig().window_capture_method == "auto"
    c = AppConfig.from_dict({"window_capture_method": "WGC"})
    assert c.window_capture_method == "wgc"
    c2 = AppConfig.from_dict({"window_capture_method": "nope"})
    assert c2.window_capture_method == "auto"


def test_is_mostly_blank_detects_black():
    assert screenshot.is_mostly_blank(_blank())
    assert screenshot.is_mostly_blank(_solid())  # uniform also blank
    assert not screenshot.is_mostly_blank(_content())


def test_client_region_to_window_image_crop_scales():
    from app.capture.windows import client_region_to_window_image_crop

    with (
        patch(
            "app.capture.windows.get_extended_frame_bounds",
            return_value=(100, 200, 500, 600),  # 400x400
        ),
        patch("win32gui.ClientToScreen", return_value=(120, 240)),  # client origin
    ):
        # frame is 200x200 (half of window bounds)
        box = client_region_to_window_image_crop(
            1, rel_x=10, rel_y=20, rel_w=40, rel_h=30, frame_w=200, frame_h=200
        )
        assert box is not None
        x1, y1, x2, y2 = box
        # client offset in window: (20, 40); + rel (10, 20) → (30, 60)
        # scale 0.5 → (15, 30); size (40, 30) → (20, 15) → bottom-right (35, 45)
        assert (x1, y1, x2, y2) == (15, 30, 35, 45)


def test_wgc_mapping_fallback_uses_client_crop(valid_hwnd):
    """When DWM mapping yields blank crop, client-style crop of WGC frame is used."""
    full = _content(100, 50, seed=9)

    with (
        patch("app.capture.wgc.capture_hwnd_wgc", return_value=full),
        patch.object(
            screenshot,
            "_crop_client_from_window_frame",
            return_value=_blank(40, 20),
        ),
        patch.object(screenshot, "capture_hwnd_client") as gdi,
        patch.object(screenshot, "capture_screen_region") as scr,
    ):
        out = screenshot.capture_region(
            hwnd=valid_hwnd,
            rel_x=0,
            rel_y=0,
            rel_w=40,
            rel_h=20,
            method="auto",
        )
        assert out.size == (40, 20)
        assert not screenshot.is_mostly_blank(out)
        gdi.assert_not_called()
        scr.assert_not_called()


def test_reasoning_effort_normalized():
    c = AppConfig.from_dict({"reasoning_effort": "HIGH"})
    assert c.reasoning_effort == "high"
    c2 = AppConfig.from_dict({"reasoning_effort": "nope"})
    assert c2.reasoning_effort == ""
