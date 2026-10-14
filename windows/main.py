"""Launch ShapMass.

Run from the repo root with:  .venv\\Scripts\\python windows\\main.py
"""
import sys
from pathlib import Path

# The shapmass package lives in python\ next to this file.
sys.path.insert(0, str(Path(__file__).resolve().parent / "python"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from shapmass.main_window import MainWindow  # noqa: E402


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ShapMass")

    window = MainWindow()
    window.show()

    # Hand control to Qt's event loop; returns when the last window closes.
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
