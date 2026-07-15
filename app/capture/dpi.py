"""Map Qt device-independent coordinates to physical (mss / Win32) pixels."""

from __future__ import annotations

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
        # Fallback: primary screen devicePixelRatio
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


def qt_rect_to_physical(
    x: int, y: int, w: int, h: int, scale: ScaleInfo | None = None
) -> tuple[int, int, int, int]:
    """Convert a Qt global logical rect to physical pixels for screen capture."""
    info = scale or probe_scale()
    if info.is_identity:
        return int(x), int(y), max(1, int(w)), max(1, int(h))
    px = int(round((x - info.qt_left) * info.scale_x + info.mss_left))
    py = int(round((y - info.qt_top) * info.scale_y + info.mss_top))
    pw = max(1, int(round(w * info.scale_x)))
    ph = max(1, int(round(h * info.scale_y)))
    return px, py, pw, ph


def physical_size_to_qt(w: int, h: int, scale: ScaleInfo | None = None) -> tuple[int, int]:
    """Scale physical size down to Qt logical size (for labels only)."""
    info = scale or probe_scale()
    if info.is_identity:
        return int(w), int(h)
    return max(1, int(round(w / info.scale_x))), max(1, int(round(h / info.scale_y)))
