"""Screenshot capture for screen regions and bound HWNDs."""

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
    """Capture entire client area of a window via PrintWindow/BitBlt."""
    try:
        import win32gui
        import win32ui
        import win32con
    except ImportError:
        return None

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

        result = user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        if result != 1:
            # Fallback BitBlt from window DC
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

        win32gui.DeleteObject(bitmap.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        return img
    except Exception:
        return None


def capture_region(
    *,
    hwnd: int | None,
    rel_x: int,
    rel_y: int,
    rel_w: int,
    rel_h: int,
    prefer_screen: bool = True,
) -> Image.Image:
    """
    Capture OCR region.

    Prefer **screen capture via mss** (works from any thread; reliable for games).
    When hwnd is valid, region is relative to client area and converted to screen.
    Optional HWND PrintWindow path only when screen grab looks blank / disabled.
    """
    if rel_w <= 0 or rel_h <= 0:
        raise ValueError("尚未框選 OCR 區域或尺寸無效")

    screen_img: Image.Image | None = None
    hwnd_img: Image.Image | None = None

    if hwnd:
        from .windows import is_window_valid, client_to_screen_rect

        if is_window_valid(hwnd):
            # Primary: screen coords of client-relative region (mss, thread-safe)
            if prefer_screen:
                try:
                    left, top, w, h = client_to_screen_rect(
                        hwnd, rel_x, rel_y, rel_w, rel_h
                    )
                    screen_img = capture_screen_region(left, top, w, h)
                    if not is_mostly_blank(screen_img):
                        return screen_img
                except Exception:
                    screen_img = None

            # Secondary: HWND bitmap crop (may fail on DX/Chrome background threads)
            full = capture_hwnd_client(hwnd)
            if full is not None:
                x2 = min(full.width, rel_x + rel_w)
                y2 = min(full.height, rel_y + rel_h)
                x1 = max(0, rel_x)
                y1 = max(0, rel_y)
                if x2 > x1 and y2 > y1:
                    hwnd_img = full.crop((x1, y1, x2, y2))
                    if not is_mostly_blank(hwnd_img):
                        return hwnd_img

            # Last resort: return least-blank available
            for candidate in (screen_img, hwnd_img):
                if candidate is not None:
                    return candidate

            # Retry screen once more even if blank
            left, top, w, h = client_to_screen_rect(hwnd, rel_x, rel_y, rel_w, rel_h)
            return capture_screen_region(left, top, w, h)

        # HWND invalid/stale: cannot convert client→screen safely
        raise ValueError(
            "綁定視窗已失效（HWND 無效）。請重新整理視窗列表並再框選 OCR 區域。"
        )

    # Absolute screen region (no binding)
    return capture_screen_region(rel_x, rel_y, rel_w, rel_h)


def capture_from_config(cfg: AppConfig) -> Image.Image:
    """Capture using AppConfig region / bound hwnd."""
    if not cfg.has_region:
        raise ValueError("尚未框選 OCR 區域")
    return capture_region(
        hwnd=cfg.bound_hwnd or None,
        rel_x=cfg.region_x,
        rel_y=cfg.region_y,
        rel_w=cfg.region_w,
        rel_h=cfg.region_h,
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


def save_last_capture(img: Image.Image) -> str:
    """Save the latest capture next to the tool for debugging OCR issues."""
    try:
        from app.config import project_root

        path = project_root() / "last_capture.png"
        img.convert("RGB").save(path)
        return str(path)
    except Exception as exc:
        return f"(save failed: {exc})"
