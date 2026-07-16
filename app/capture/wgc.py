"""Windows Graphics Capture (WGC) single-frame window capture.

Uses the ``windows-capture`` package (Win10 1903+ Graphics Capture API),
aligned with OBS Window Capture method "Windows Graphics Capture".

Fails soft (returns None) when the package is missing, OS unsupported,
or the target HWND cannot be captured — callers fall back to GDI/screen.
"""

from __future__ import annotations

import threading

from PIL import Image

_WGC_TIMEOUT_S = 2.5
_wgc_lock = threading.Lock()
_gen_counter = 0
_gen_lock = threading.Lock()


def wgc_available() -> bool:
    """True when the windows-capture binding imports successfully."""
    try:
        from windows_capture import WindowsCapture  # noqa: F401

        return True
    except Exception:
        return False


def capture_hwnd_wgc(hwnd: int, *, timeout_s: float = _WGC_TIMEOUT_S) -> Image.Image | None:
    """
    Capture one full-window frame for *hwnd* via WGC.

    Returns RGB PIL Image, or None on any failure.
    Thread-safe; serializes capture sessions (WGC dislikes concurrent sessions).
    """
    if not hwnd:
        return None
    try:
        from windows_capture import Frame, InternalCaptureControl, WindowsCapture
    except Exception:
        return None

    with _wgc_lock:
        return _capture_once(
            int(hwnd), float(timeout_s), WindowsCapture, Frame, InternalCaptureControl
        )


def _next_gen() -> int:
    global _gen_counter
    with _gen_lock:
        _gen_counter += 1
        return _gen_counter


def _capture_once(
    hwnd: int,
    timeout_s: float,
    WindowsCapture: type,
    Frame: type,
    InternalCaptureControl: type,
) -> Image.Image | None:
    result: dict[str, Image.Image | None] = {"img": None}
    done = threading.Event()
    gen = _next_gen()
    cancelled = {"v": False}

    try:
        # HWND only — no window_name fallback (substring match can hit the wrong window)
        capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_hwnd=int(hwnd),
        )
    except Exception:
        return None

    def on_frame_arrived(frame: Frame, capture_control: InternalCaptureControl) -> None:
        if cancelled["v"]:
            try:
                capture_control.stop()
            except Exception:
                pass
            done.set()
            return
        try:
            buf = getattr(frame, "frame_buffer", None)
            if buf is None:
                return
            bgr = buf[:, :, :3]
            rgb = bgr[:, :, ::-1].copy()
            if not cancelled["v"]:
                result["img"] = Image.fromarray(rgb, mode="RGB")
        except Exception:
            if not cancelled["v"]:
                result["img"] = None
        finally:
            try:
                capture_control.stop()
            except Exception:
                pass
            done.set()

    def on_closed() -> None:
        done.set()

    try:
        capture.frame_handler = on_frame_arrived
        capture.closed_handler = on_closed
    except Exception:
        return None

    control = None
    try:
        if hasattr(capture, "start_free_threaded"):
            control = capture.start_free_threaded()
        else:

            def _run() -> None:
                try:
                    capture.start()
                except Exception:
                    done.set()

            threading.Thread(target=_run, name="wgc-capture", daemon=True).start()

        if not done.wait(timeout_s):
            cancelled["v"] = True
            try:
                if control is not None:
                    control.stop()
            except Exception:
                pass

        # Always wait for the capture thread to finish before releasing the lock
        try:
            if control is not None and hasattr(control, "wait"):
                control.wait()
        except Exception:
            pass

        if cancelled["v"]:
            return None

        img = result["img"]
        if img is None or img.width < 2 or img.height < 2:
            return None
        # Ignore if a newer generation was started (shouldn't happen under lock)
        _ = gen
        return img
    except Exception:
        cancelled["v"] = True
        try:
            if control is not None:
                control.stop()
                if hasattr(control, "wait"):
                    control.wait()
        except Exception:
            pass
        return None
