"""Win32 window enumeration and geometry helpers."""

from __future__ import annotations

from dataclasses import dataclass

import win32con
import win32gui
import win32process


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int

    def __str__(self) -> str:
        return f"{self.title}  [hwnd={self.hwnd}]"


def enum_windows(*, min_title_len: int = 1) -> list[WindowInfo]:
    """List visible top-level windows with non-empty titles."""
    results: list[WindowInfo] = []

    def _callback(hwnd: int, _extra: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.GetParent(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd) or ""
        title = title.strip()
        if len(title) < min_title_len:
            return True
        # Skip tool windows without caption if empty already handled
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0
        results.append(WindowInfo(hwnd=int(hwnd), title=title, pid=int(pid)))
        return True

    win32gui.EnumWindows(_callback, None)
    results.sort(key=lambda w: w.title.lower())
    return results


def is_window_valid(hwnd: int) -> bool:
    if not hwnd:
        return False
    try:
        return bool(win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd))
    except Exception:
        return False


def get_window_title(hwnd: int) -> str:
    try:
        return (win32gui.GetWindowText(hwnd) or "").strip()
    except Exception:
        return ""


def get_client_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Return client rect as (left, top, right, bottom) in client coordinates."""
    return win32gui.GetClientRect(hwnd)


def client_to_screen_rect(
    hwnd: int, rel_x: int, rel_y: int, rel_w: int, rel_h: int
) -> tuple[int, int, int, int]:
    """Convert region relative to client area into screen (left, top, width, height)."""
    left_top = win32gui.ClientToScreen(hwnd, (rel_x, rel_y))
    return left_top[0], left_top[1], rel_w, rel_h


def screen_region_to_client(
    hwnd: int, screen_x: int, screen_y: int, w: int, h: int
) -> tuple[int, int, int, int]:
    """Convert screen region to client-relative (x, y, w, h), clamped to client."""
    cl, ct, cr, cb = get_client_rect(hwnd)
    origin = win32gui.ClientToScreen(hwnd, (0, 0))
    rx = screen_x - origin[0]
    ry = screen_y - origin[1]
    # Clamp
    rx = max(cl, min(rx, cr - 1))
    ry = max(ct, min(ry, cb - 1))
    max_w = max(1, cr - rx)
    max_h = max(1, cb - ry)
    w = max(1, min(w, max_w))
    h = max(1, min(h, max_h))
    return rx, ry, w, h


def get_window_screen_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Full window rect (including frame) as left, top, right, bottom screen coords."""
    return win32gui.GetWindowRect(hwnd)


def get_extended_frame_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    """
    DWM extended frame bounds (screen L,T,R,B) — often matches WGC frame better
    than GetWindowRect (excludes drop shadow).
    """
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        rect = RECT()
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(int(hwnd)),
            ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if hr != 0:
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:
        return None


def client_region_to_window_image_crop(
    hwnd: int,
    rel_x: int,
    rel_y: int,
    rel_w: int,
    rel_h: int,
    *,
    frame_w: int,
    frame_h: int,
) -> tuple[int, int, int, int] | None:
    """
    Map client-relative OCR region to crop box (x1,y1,x2,y2) inside a full-window
    capture of size (frame_w, frame_h). Uses DWM bounds when available.
    """
    if frame_w <= 0 or frame_h <= 0 or rel_w <= 0 or rel_h <= 0:
        return None
    try:
        bounds = get_extended_frame_bounds(hwnd)
        if bounds is None:
            wl, wt, wr, wb = get_window_screen_rect(hwnd)
        else:
            wl, wt, wr, wb = bounds
        win_w = max(1, wr - wl)
        win_h = max(1, wb - wt)
        origin = win32gui.ClientToScreen(hwnd, (0, 0))
        # Client (0,0) offset within window bounds (screen space)
        ox = origin[0] - wl
        oy = origin[1] - wt
        # Scale window coords → captured frame pixels
        sx = frame_w / float(win_w)
        sy = frame_h / float(win_h)
        x1 = int(round((ox + rel_x) * sx))
        y1 = int(round((oy + rel_y) * sy))
        x2 = int(round((ox + rel_x + rel_w) * sx))
        y2 = int(round((oy + rel_y + rel_h) * sy))
        x1 = max(0, min(x1, frame_w - 1))
        y1 = max(0, min(y1, frame_h - 1))
        x2 = max(x1 + 1, min(x2, frame_w))
        y2 = max(y1 + 1, min(y2, frame_h))
        return x1, y1, x2, y2
    except Exception:
        return None


def bring_window_to_front(hwnd: int) -> None:
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
