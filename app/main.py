"""GalMaster entry point."""

from __future__ import annotations

import sys


def main() -> int:
    # Do NOT call SetProcessDpiAwareness* here — Qt sets it once at startup.
    # Calling it first causes: qt.qpa.window: SetProcessDpiAwarenessContext() failed.
    # Capture maps Qt DIP → physical pixels in app.capture.dpi.

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QStyleFactory

    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    from app.app_controller import AppController
    from app.ui.styles import MAIN_STYLE, apply_dark_palette

    app = QApplication(sys.argv)
    app.setApplicationName("GalMaster")
    app.setQuitOnLastWindowClosed(False)

    # windows11/vista native styles break QSS combo/spin arrows (empty strips).
    # Fusion + explicit QSS indicators is the reliable dark-UI path on Windows.
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    apply_dark_palette(app)
    app.setStyleSheet(MAIN_STYLE)

    controller = AppController(app)
    app._galmaster = controller  # type: ignore[attr-defined]

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
