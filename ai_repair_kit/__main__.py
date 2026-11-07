"""Entry point for the desktop application."""

import sys

from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .gui.window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    QCoreApplication.setOrganizationName("AIRepairKit")
    QCoreApplication.setApplicationName("AI Repair Kit")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))

    window = MainWindow(app, QSettings())
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
