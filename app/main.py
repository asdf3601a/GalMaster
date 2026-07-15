"""GalMaster entry point."""

from __future__ import annotations

import sys


def main() -> int:
    # Do NOT call SetProcessDpiAwareness* here — Qt sets it once at startup.
    # Calling it first causes: qt.qpa.window: SetProcessDpiAwarenessContext() failed.
    # Capture maps Qt DIP → physical pixels in app.capture.dpi.

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    from app.app_controller import AppController

    app = QApplication(sys.argv)
    app.setApplicationName("GalMaster")
    app.setQuitOnLastWindowClosed(False)

    controller = AppController(app)
    app._galmaster = controller  # type: ignore[attr-defined]

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
