"""Screenshot capture for screen regions and bound HWNDs.

Capture policy (aligned with OBS Window Capture Automatic):
  - No bound HWND / full-screen region → display capture (mss → GDI desktop → ImageGrab)
  - Bound HWND → window capture: WGC → BitBlt/PrintWindow → screen fallback
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import TYPE_CHECKING

import mss
import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from app.config import AppConfig

# Win32 PrintWindow / BitBlt helpers
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
PW_RENDERFULLCONTENT = 0x00000002

_VALID_METHODS = frozenset({"auto", "wgc", "bitblt"})


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def capture_screen_region(left: int, top: int, width: int, height: int) -> Image.Image:
    """
    Capture a screen region. Coordinates are **physical** virtual-screen pixels
    (mss / Win32), not Qt device-independent pixels.
    """
    if width <= 0 or height <= 0:
        raise ValueError("Capture region must have positive size")
    left, top, width, height = int(left), int(top), int(width), int(height)
    errors: list[str] = []

    # 1) mss (preferred when GDI works)
    try:
        sct_cls = getattr(mss, "MSS", None) or mss.mss
        with sct_cls() as sct:
            monitor = {
                "left": left,
                "top": top,
                "width": width,
                "height": height,
            }
            shot = sct.grab(monitor)
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception as exc:
        errors.append(f"mss: {exc}")

    # 2) Win32 BitBlt from desktop DC
    try:
        img = _capture_gdi_desktop(left, top, width, height)
        if img is not None:
            return img
        errors.append("gdi: empty")
    except Exception as exc:
        errors.append(f"gdi: {exc}")

    # 3) PIL ImageGrab (bbox is physical on Windows)
    try:
        from PIL import ImageGrab

        img = ImageGrab.grab(bbox=(left, top, left + width, top + height))
        if img is not None:
            return img.convert("RGB")
        errors.append("ImageGrab: empty")
    except Exception as exc:
        errors.append(f"ImageGrab: {exc}")

    raise RuntimeError(
        "螢幕截圖失敗（" + " | ".join(errors) + "）。"
        "請確認不是遠端/沙箱環境，且 GalMaster 有螢幕擷取權限。"
    )


def _capture_gdi_desktop(left: int, top: int, width: int, height: int) -> Image.Image | None:
    """Fallback desktop BitBlt capture."""
    try:
        import win32con
        import win32gui
        import win32ui
    except ImportError:
        return None

    hdesktop = win32gui.GetDesktopWindow()
    hwnd_dc = win32gui.GetWindowDC(hdesktop)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    try:
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bitmap)
        save_dc.BitBlt((0, 0), (width, height), mfc_dc, (left, top), win32con.SRCCOPY)
        bmpinfo = bitmap.GetInfo()
        bmpstr = bitmap.GetBitmapBits(True)
        return Image.frombuffer(
            "RGB",
            (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
            bmpstr,
            "raw",
            "BGRX",
            0,
            1,
        ).copy()
    finally:
        try:
            win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            pass
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hdesktop, hwnd_dc)


def is_mostly_blank(img: Image.Image, *, threshold: float = 0.02) -> bool:
    """
    True when frame is near-uniform (black/white/empty capture).
    Used to detect failed HWND captures so we can fall back to screen grab.
    """
    if img.width < 2 or img.height < 2:
        return True
    # Downsample for speed
    small = img.convert("L").resize(
        (min(64, img.width), min(64, img.height)),
        Image.Resampling.BILINEAR,
    )
    arr = np.asarray(small, dtype=np.float32)
    std = float(np.std(arr))
    # Near-zero variance → solid color (failed PrintWindow often black)
    if std < 3.0:
        return True
    # Extremely dark with almost no signal
    mean = float(np.mean(arr))
    if mean < 4.0 and std < 8.0:
        return True
    # Extremely bright solid
    if mean > 251.0 and std < 8.0:
        return True
    return std / 255.0 < threshold and (mean < 8.0 or mean > 247.0)


def capture_hwnd_client(hwnd: int) -> Image.Image | None:
    """
    Capture entire client area of a window via PrintWindow / BitBlt (GDI).

    Order (OBS BitBlt-family):
      1) PrintWindow + PW_RENDERFULLCONTENT
      2) PrintWindow flag 0
      3) BitBlt from window DC
    """
    try:
        import win32con
        import win32gui
        import win32ui
    except ImportError:
        return None

    hwnd_dc = None
    mfc_dc = None
    save_dc = None
    bitmap = None
    try:
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            return None

        hwnd_dc = win32gui.GetDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bitmap)

        hdc = save_dc.GetSafeHdc()
        ok = user32.PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT)
        if ok != 1:
            ok = user32.PrintWindow(hwnd, hdc, 0)
        if ok != 1:
            save_dc.BitBlt((0, 0), (w, h), mfc_dc, (0, 0), win32con.SRCCOPY)

        bmpinfo = bitmap.GetInfo()
        bmpstr = bitmap.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGB",
            (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
            bmpstr,
            "raw",
            "BGRX",
            0,
            1,
        ).copy()
        return img
    except Exception:
        return None
    finally:
        try:
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            pass
        try:
            if save_dc is not None:
                save_dc.DeleteDC()
        except Exception:
            pass
        try:
            if mfc_dc is not None:
                mfc_dc.DeleteDC()
        except Exception:
            pass
        try:
            if hwnd_dc is not None:
                win32gui.ReleaseDC(hwnd, hwnd_dc)
        except Exception:
            pass


def _crop_client_from_hwnd_image(
    full: Image.Image,
    rel_x: int,
    rel_y: int,
    rel_w: int,
    rel_h: int,
) -> Image.Image | None:
    """Crop client-relative region from a client-area bitmap."""
    x2 = min(full.width, rel_x + rel_w)
    y2 = min(full.height, rel_y + rel_h)
    x1 = max(0, rel_x)
    y1 = max(0, rel_y)
    if x2 > x1 and y2 > y1:
        return full.crop((x1, y1, x2, y2))
    return None


def _crop_client_from_window_frame(
    hwnd: int,
    full: Image.Image,
    rel_x: int,
    rel_y: int,
    rel_w: int,
    rel_h: int,
) -> Image.Image | None:
    """Crop client-relative region from a full-window WGC frame."""
    from .windows import client_region_to_window_image_crop

    box = client_region_to_window_image_crop(
        hwnd,
        rel_x,
        rel_y,
        rel_w,
        rel_h,
        frame_w=full.width,
        frame_h=full.height,
    )
    if box is None:
        return None
    x1, y1, x2, y2 = box
    if x2 > x1 and y2 > y1:
        return full.crop((x1, y1, x2, y2))
    return None


def _normalize_method(method: str | None) -> str:
    m = (method or "auto").strip().lower()
    return m if m in _VALID_METHODS else "auto"


def capture_region(
    *,
    hwnd: int | None,
    rel_x: int,
    rel_y: int,
    rel_w: int,
    rel_h: int,
    prefer_screen: bool = False,
    abs_x: int = 0,
    abs_y: int = 0,
    abs_w: int = 0,
    abs_h: int = 0,
    method: str = "auto",
) -> Image.Image:
    """
    Capture OCR region.

    Bound HWND (window capture, OBS Automatic order unless overridden):
      WGC → GDI PrintWindow/BitBlt → screen region fallback

    No HWND: absolute / relative screen region via mss.

    *prefer_screen* is legacy; when True and hwnd set, skips WGC/GDI and uses
    screen coords only (debug). Default False.
    """
    if rel_w <= 0 or rel_h <= 0:
        if abs_w > 0 and abs_h > 0:
            return capture_screen_region(abs_x, abs_y, abs_w, abs_h)
        raise ValueError("尚未框選 OCR 區域或尺寸無效")

    method = _normalize_method(method)

    if hwnd:
        from .windows import client_to_screen_rect, is_window_valid

        if is_window_valid(hwnd):
            if prefer_screen:
                left, top, w, h = client_to_screen_rect(
                    hwnd, rel_x, rel_y, rel_w, rel_h
                )
                return capture_screen_region(left, top, w, h)

            wgc_img: Image.Image | None = None
            gdi_img: Image.Image | None = None
            try_wgc = method in ("auto", "wgc")
            try_gdi = method in ("auto", "bitblt", "wgc")  # wgc still falls back to GDI

            # 1) Windows Graphics Capture
            if try_wgc:
                try:
                    from .wgc import capture_hwnd_wgc

                    full = capture_hwnd_wgc(int(hwnd))
                    if full is not None and not is_mostly_blank(full):
                        cropped = _crop_client_from_window_frame(
                            int(hwnd), full, rel_x, rel_y, rel_w, rel_h
                        )
                        if cropped is not None and not is_mostly_blank(cropped):
                            return cropped
                        # Mapping failed: try client-style crop (frame ≈ client)
                        alt = _crop_client_from_hwnd_image(
                            full, rel_x, rel_y, rel_w, rel_h
                        )
                        if alt is not None and not is_mostly_blank(alt):
                            return alt
                        # Whole-client region: accept full frame
                        try:
                            from .windows import get_client_rect

                            _cl, _ct, cr, cb = get_client_rect(int(hwnd))
                            cw, ch = max(1, cr), max(1, cb)
                            if rel_x <= 2 and rel_y <= 2 and rel_w >= cw - 4 and rel_h >= ch - 4:
                                return full
                        except Exception:
                            pass
                        wgc_img = alt or cropped
                except Exception:
                    wgc_img = None

            # 2) GDI PrintWindow / BitBlt
            if try_gdi:
                full = capture_hwnd_client(int(hwnd))
                if full is not None:
                    cropped = _crop_client_from_hwnd_image(
                        full, rel_x, rel_y, rel_w, rel_h
                    )
                    if cropped is not None and not is_mostly_blank(cropped):
                        return cropped
                    gdi_img = cropped

            # Prefer least-blank intermediate before screen
            for candidate in (wgc_img, gdi_img):
                if candidate is not None and not is_mostly_blank(candidate):
                    return candidate

            # 3) Screen region fallback (display capture)
            left, top, w, h = client_to_screen_rect(hwnd, rel_x, rel_y, rel_w, rel_h)
            return capture_screen_region(left, top, w, h)

        # HWND invalid/stale: use last absolute screen rect if known
        if abs_w > 0 and abs_h > 0:
            return capture_screen_region(int(abs_x), int(abs_y), int(abs_w), int(abs_h))
        raise ValueError(
            "綁定視窗已失效（HWND 無效），且沒有可用的螢幕座標快取。"
            "請重新整理視窗列表並再框選 OCR 區域。"
        )

    # Absolute screen region (no binding) — display capture only
    return capture_screen_region(rel_x, rel_y, rel_w, rel_h)


def capture_from_config(
    cfg: AppConfig,
    *,
    method: str | None = None,
) -> Image.Image:
    """
    Capture using AppConfig region / bound hwnd / last absolute screen rect.

    *method* overrides ``cfg.window_capture_method`` when set (e.g. monitor
    polls force BitBlt while translation capture keeps Automatic/WGC).
    """
    if not cfg.has_region and not getattr(cfg, "has_abs_region", False):
        raise ValueError("尚未框選 OCR 區域")
    use_method = (
        method
        if method is not None
        else str(getattr(cfg, "window_capture_method", "auto") or "auto")
    )
    return capture_region(
        hwnd=cfg.bound_hwnd or None,
        rel_x=cfg.region_x,
        rel_y=cfg.region_y,
        rel_w=cfg.region_w,
        rel_h=cfg.region_h,
        abs_x=int(getattr(cfg, "region_abs_x", 0) or 0),
        abs_y=int(getattr(cfg, "region_abs_y", 0) or 0),
        abs_w=int(getattr(cfg, "region_abs_w", 0) or 0),
        abs_h=int(getattr(cfg, "region_abs_h", 0) or 0),
        method=use_method,
    )


def image_to_gray_array(img: Image.Image, max_side: int = 320) -> np.ndarray:
    """Downscale grayscale array for change detection."""
    gray = img.convert("L")
    w, h = gray.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        gray = gray.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.BILINEAR,
        )
    return np.asarray(gray, dtype=np.float32)


def mean_abs_diff(a: np.ndarray, b: np.ndarray) -> float:
    """Normalized mean absolute difference in [0, 1]."""
    if a.shape != b.shape:
        from PIL import Image as PILImage

        bi = PILImage.fromarray(b.astype(np.uint8))
        bi = bi.resize((a.shape[1], a.shape[0]), PILImage.Resampling.BILINEAR)
        b = np.asarray(bi, dtype=np.float32)
    return float(np.mean(np.abs(a - b)) / 255.0)


def describe_image(img: Image.Image) -> str:
    """Short diagnostic for error messages."""
    arr = np.asarray(img.convert("L"), dtype=np.float32)
    return (
        f"{img.width}×{img.height} mean={float(np.mean(arr)):.0f} "
        f"std={float(np.std(arr)):.0f}"
    )


def make_preview_image(img: Image.Image, *, max_edge: int = 480) -> Image.Image:
    """Downscale a copy for the main-window capture preview (no disk write)."""
    preview = img.convert("RGB")
    w, h = preview.size
    edge = max(w, h)
    if edge > max_edge > 0:
        scale = max_edge / float(edge)
        preview = preview.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.LANCZOS,
        )
    return preview


def save_last_capture(img: Image.Image) -> str:
    """Deprecated: no longer writes last_capture.png; kept for call-site compatibility."""
    _ = img
    return ""
