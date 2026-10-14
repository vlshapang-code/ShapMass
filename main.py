"""Launch ShapMass.

Run with:  .venv/bin/python main.py
"""
import sys

from PySide6.QtWidgets import QApplication

from shapmass.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ShapMass")

    window = MainWindow()
    window.show()

    # Hand control to Qt's event loop; returns when the last window closes.
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
