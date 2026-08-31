from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from . import __version__
from .ui import APP_NAME, MainWindow


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("EDTT Definer")
    window = MainWindow()
    window.show()
    return app.exec()
