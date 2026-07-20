"""Shared pytest fixtures."""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QStyleFactory  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    """Single process-wide QApplication for widget and signal tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv if sys.argv else ["pytest"])
    if not isinstance(app, QApplication):
        pytest.fail(
            "QApplication required; a non-QApplication QCoreApplication "
            "already exists in this process"
        )
    # QStyleFactory.keys() is a Qt API (not dict.keys()); materialize for membership.
    style_names = list(QStyleFactory.keys())
    if "Fusion" in style_names:
        app.setStyle("Fusion")
    return app
