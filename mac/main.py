"""Launch ShapMass.

Run with any Python:  python3 mac/main.py
If it isn't the repo's .venv interpreter, it re-launches itself with .venv.
"""
import os
import sys
from pathlib import Path

# Re-exec under the repo's .venv so PySide6 is always found, no matter which
# Python started this script. Must happen before importing PySide6.
_VENV_PYTHON = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python"
if _VENV_PYTHON.exists() and Path(sys.prefix).resolve() != _VENV_PYTHON.parent.parent.resolve():
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), *sys.argv])

from PySide6.QtWidgets import QApplication

# The app's modules live in python/ next to this file.
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
