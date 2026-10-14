"""Launch ShapMass.

Run from the repo root with:  .venv\\python.exe windows\\main.py
"""
import sys

from PySide6.QtWidgets import QApplication

# The app's modules live in python\ next to this file.
from python.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ShapMass")

    window = MainWindow()
    window.show()

    # Hand control to Qt's event loop; returns when the last window closes.
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
