"""Launch ShapMass.

Run from the repo root with:  python windows\\main.py
(any Python works; it hands itself over to .venv\\python.exe)
"""
import subprocess
import sys
from pathlib import Path

# If started by a Python other than the project's .venv (e.g. the WindowsApps stub,
# which has no PySide6), re-run this script with .venv\python.exe and pass on its exit code.
VENV_PYTHON = Path(__file__).resolve().parent.parent / ".venv" / "python.exe"
if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    sys.exit(subprocess.call([str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]))

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
