from .dpi import probe_scale, qt_rect_to_physical
from .monitor import RegionMonitor
from .screenshot import capture_region
from .windows import WindowInfo, client_to_screen_rect, enum_windows, get_client_rect

try:
    from .wgc import capture_hwnd_wgc, wgc_available
except Exception:  # pragma: no cover
    def capture_hwnd_wgc(*_a, **_k):  # type: ignore[misc]
        return None

    def wgc_available() -> bool:  # type: ignore[misc]
        return False

__all__ = [
    "RegionMonitor",
    "WindowInfo",
    "capture_hwnd_wgc",
    "capture_region",
    "client_to_screen_rect",
    "enum_windows",
    "get_client_rect",
    "probe_scale",
    "qt_rect_to_physical",
    "wgc_available",
]
