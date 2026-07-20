"""Map Qt device-independent coordinates to physical (mss / Win32) pixels."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass


@dataclass(frozen=True)
class ScaleInfo:
    """How Qt virtual desktop maps onto the mss virtual screen."""

    scale_x: float = 1.0
    scale_y: float = 1.0
    qt_left: int = 0
    qt_top: int = 0
    mss_left: int = 0
    mss_top: int = 0

    @property
    def is_identity(self) -> bool:
        return (
            abs(self.scale_x - 1.0) < 0.01
            and abs(self.scale_y - 1.0) < 0.01
            and self.qt_left == self.mss_left
            and self.qt_top == self.mss_top
        )


def probe_scale() -> ScaleInfo:
    """
    Compare Qt screen geometry (logical / DIP) with mss (physical pixels).

    On a 150% display, Qt often reports 2560×1440 while mss reports 3840×2160.
    Capture APIs (mss, ClientToScreen when DPI-aware) need physical pixels.
    """
    try:
        from PySide6.QtGui import QGuiApplication
    except Exception:
        return ScaleInfo()

    screens = QGuiApplication.screens()
    if not screens:
        return ScaleInfo()

    qt_left = min(s.geometry().left() for s in screens)
    qt_top = min(s.geometry().top() for s in screens)
    qt_right = max(s.geometry().right() for s in screens)
    qt_bottom = max(s.geometry().bottom() for s in screens)
    qt_w = max(1, qt_right - qt_left + 1)
    qt_h = max(1, qt_bottom - qt_top + 1)

    try:
        import mss

        with mss.mss() as sct:
            virt = sct.monitors[0]
            mss_left = int(virt["left"])
            mss_top = int(virt["top"])
            mss_w = max(1, int(virt["width"]))
            mss_h = max(1, int(virt["height"]))
    except Exception:
        try:
            primary = QGuiApplication.primaryScreen()
            dpr = float(primary.devicePixelRatio()) if primary else 1.0
        except Exception:
            dpr = 1.0
        return ScaleInfo(scale_x=dpr, scale_y=dpr, qt_left=qt_left, qt_top=qt_top)

    return ScaleInfo(
        scale_x=mss_w / qt_w,
        scale_y=mss_h / qt_h,
        qt_left=qt_left,
        qt_top=qt_top,
        mss_left=mss_left,
        mss_top=mss_top,
    )


def _screens_mixed_dpi() -> bool:
    try:
        from PySide6.QtGui import QGuiApplication

        screens = QGuiApplication.screens()
        if not screens or len(screens) < 2:
            return False
        dprs = {round(float(s.devicePixelRatio()), 3) for s in screens}
        return len(dprs) > 1
    except Exception:
        return False


def _match_qt_screens_to_mss() -> list[tuple[object, dict]] | None:
    """
    Pair each QScreen with an mss monitor dict by sorted (left, top).

    Returns list of (QScreen, mss_monitor) or None if counts mismatch / mss fails.
    """
    try:
        import mss
        from PySide6.QtGui import QGuiApplication
    except Exception:
        return None

    screens = list(QGuiApplication.screens() or [])
    if not screens:
        return None
    try:
        with mss.mss() as sct:
            # monitors[0] is the virtual desktop; [1:] are physical displays
            mons = list(sct.monitors[1:])
    except Exception:
        return None
    if len(mons) != len(screens):
        return None
    q_sorted = sorted(screens, key=lambda s: (s.geometry().left(), s.geometry().top()))
    m_sorted = sorted(mons, key=lambda m: (int(m["left"]), int(m["top"])))
    return list(zip(q_sorted, m_sorted, strict=False))


def _screen_at_logical(x: int, y: int):
    """QScreen containing logical point, or nearest screen center."""
    try:
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QGuiApplication
    except Exception:
        return None

    screens = list(QGuiApplication.screens() or [])
    if not screens:
        return None
    screen = QGuiApplication.screenAt(QPoint(int(x), int(y)))
    if screen is not None:
        return screen

    def dist2(s) -> float:
        g = s.geometry()
        cx = g.left() + g.width() / 2.0
        cy = g.top() + g.height() / 2.0
        return (cx - x) ** 2 + (cy - y) ** 2

    return min(screens, key=dist2)


def _logical_point_to_physical_mixed(x: int, y: int) -> tuple[int, int] | None:
    """
    Map one logical (DIP) point to physical pixels under mixed per-monitor DPI.

    Prefer per-screen mss rect + that screen's geometry scale (not primary-only
    LogicalToPhysicalPointForPerMonitorDPI(NULL)).
    """
    pairs = _match_qt_screens_to_mss()
    screen = _screen_at_logical(x, y)
    if screen is None:
        return None

    if pairs:
        mon = None
        for qs, m in pairs:
            if qs is screen:
                mon = m
                break
        if mon is not None:
            g = screen.geometry()
            gw = max(1, g.width())
            gh = max(1, g.height())
            mx = float(mon["left"])
            my = float(mon["top"])
            mw = float(max(1, mon["width"]))
            mh = float(max(1, mon["height"]))
            px = int(round(mx + (x - g.left()) * (mw / gw)))
            py = int(round(my + (y - g.top()) * (mh / gh)))
            return px, py

    # Fallback: per-screen devicePixelRatio relative to that screen's logical origin.
    # Physical origin approximated via Win32 when possible.
    dpr = float(screen.devicePixelRatio())
    g = screen.geometry()
    phys = _win_logical_to_physical_for_screen(int(x), int(y), screen)
    if phys is not None:
        return phys
    # Last resort: scale within screen from primary-based virtual origin
    info = probe_scale()
    px = int(round((x - info.qt_left) * dpr + info.mss_left))
    py = int(round((y - info.qt_top) * dpr + info.mss_top))
    return px, py


def _win_logical_to_physical_for_screen(
    x: int, y: int, screen
) -> tuple[int, int] | None:
    """
    Use LogicalToPhysicalPointForPerMonitorDPI with an HWND on the target monitor.

    Passing hwnd=NULL always uses the primary monitor DPI — avoid that.
    """
    try:
        user32 = ctypes.windll.user32
        if not hasattr(user32, "LogicalToPhysicalPointForPerMonitorDPI"):
            return None
        hwnd = _hwnd_on_screen(screen)
        pt = wintypes.POINT(int(x), int(y))
        ok = user32.LogicalToPhysicalPointForPerMonitorDPI(hwnd, ctypes.byref(pt))
        if not ok:
            return None
        return int(pt.x), int(pt.y)
    except Exception:
        return None


def _hwnd_on_screen(screen) -> int | None:
    """Find a top-level HWND whose center lies on the given QScreen."""
    try:
        user32 = ctypes.windll.user32
        g = screen.geometry()
        # Seed with a physical-ish guess using that screen's DPR for WindowFromPoint
        dpr = float(screen.devicePixelRatio())
        cx = int(round((g.left() + g.width() / 2) * dpr))
        cy = int(round((g.top() + g.height() / 2) * dpr))
        pt = wintypes.POINT(cx, cy)
        hwnd = user32.WindowFromPoint(pt)
        if hwnd:
            # Climb to root owner
            GA_ROOT = 2
            root = (
                user32.GetAncestor(hwnd, GA_ROOT)
                if hasattr(user32, "GetAncestor")
                else hwnd
            )
            return int(root or hwnd)
    except Exception:
        pass
    return None


def _win_logical_to_physical(x: int, y: int) -> tuple[int, int] | None:
    """Per-monitor DIP → physical via screen-aware path (not primary-only NULL hwnd)."""
    return _logical_point_to_physical_mixed(int(x), int(y))


def qt_rect_to_physical(
    x: int, y: int, w: int, h: int, scale: ScaleInfo | None = None
) -> tuple[int, int, int, int]:
    """
    Convert a Qt global logical rect to physical pixels for screen capture.

    Default path uses uniform virtual-desktop scale (probe_scale) — proven on
    single-DPI setups (e.g. 150%). Mixed-DPI multi-monitor uses per-screen mss
    mapping. Explicit `scale` always wins (tests / callers).
    """
    if scale is not None:
        if scale.is_identity:
            return int(x), int(y), max(1, int(w)), max(1, int(h))
        px = int(round((x - scale.qt_left) * scale.scale_x + scale.mss_left))
        py = int(round((y - scale.qt_top) * scale.scale_y + scale.mss_top))
        pw = max(1, int(round(w * scale.scale_x)))
        ph = max(1, int(round(h * scale.scale_y)))
        return px, py, pw, ph

    # Mixed per-monitor DPI: map corners via screen-aware conversion
    if _screens_mixed_dpi():
        tl = _win_logical_to_physical(int(x), int(y))
        br = _win_logical_to_physical(int(x + w), int(y + h))
        if tl is not None and br is not None:
            px, py = tl
            pw = max(1, br[0] - tl[0])
            ph = max(1, br[1] - tl[1])
            return px, py, pw, ph

    # Single (or uniform) DPI: mss vs Qt virtual desktop scale
    info = probe_scale()
    if info.is_identity:
        return int(x), int(y), max(1, int(w)), max(1, int(h))
    px = int(round((x - info.qt_left) * info.scale_x + info.mss_left))
    py = int(round((y - info.qt_top) * info.scale_y + info.mss_top))
    pw = max(1, int(round(w * info.scale_x)))
    ph = max(1, int(round(h * info.scale_y)))
    return px, py, pw, ph


def physical_size_to_qt(
    w: int, h: int, scale: ScaleInfo | None = None
) -> tuple[int, int]:
    """Scale physical size down to Qt logical size (for labels only)."""
    info = scale or probe_scale()
    if info.is_identity:
        return int(w), int(h)
    return max(1, int(round(w / info.scale_x))), max(1, int(round(h / info.scale_y)))
