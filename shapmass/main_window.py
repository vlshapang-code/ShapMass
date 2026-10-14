"""The main application window.

Layout:
    - Center: the TIC plot (fixed in place).
    - Docks:  spectrum windows. Each one can be dragged around, docked to any
              edge, stacked into tabs, floated as its own window, or closed.

Stage 1 is layout only; no data is read from the .raw file yet.
"""
from pathlib import Path

# PySide6 must be imported before pyqtgraph so pyqtgraph picks the same Qt binding.
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QDockWidget, QFileDialog, QMainWindow

import pyqtgraph as pg

# White background / black axes, like most mass spec software.
pg.setConfigOptions(background="w", foreground="k", antialias=False)


def make_plot(x_label, y_label):
    """Create an empty zoomable plot with labeled axes."""
    plot = pg.PlotWidget()
    plot.setLabel("bottom", x_label)
    plot.setLabel("left", y_label)
    plot.showGrid(x=True, y=True, alpha=0.2)
    return plot


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ShapMass")
        self.resize(1200, 800)
        # Allow docks to be split side-by-side within the same edge.
        self.setDockNestingEnabled(True)

        self.raw_path = None      # Path of the currently open .raw file
        self.spectrum_count = 0   # Used to number new spectrum windows

        self.tic_plot = make_plot("Retention time (min)", "Intensity")
        self.tic_plot.setTitle("TIC")
        self.setCentralWidget(self.tic_plot)

        self._build_menus()
        self.add_spectrum_dock()
        self.statusBar().showMessage("File → Open… to load a Thermo .raw file")

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("&File")

        open_action = QAction("&Open…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)  # Cmd+O
        open_action.triggered.connect(self.open_file_dialog)
        file_menu.addAction(open_action)

        # On macOS, Qt moves this into the app menu automatically (Cmd+Q).
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("&View")

        new_spectrum_action = QAction("New Spectrum Window", self)
        new_spectrum_action.setShortcut(QKeySequence("Ctrl+N"))  # Cmd+N on macOS
        new_spectrum_action.triggered.connect(self.add_spectrum_dock)
        view_menu.addAction(new_spectrum_action)

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Thermo RAW file", "", "Thermo RAW files (*.raw);;All files (*)"
        )
        if path:  # Empty string means the user cancelled
            self.open_file(Path(path))

    def open_file(self, path):
        # Stage 1: just remember the path. Stage 2 will read the TIC from it.
        self.raw_path = path
        self.setWindowTitle(f"ShapMass — {path.name}")
        self.statusBar().showMessage(f"Selected {path} (reading not implemented yet)")

    def add_spectrum_dock(self):
        """Add a new, empty, movable spectrum window at the bottom."""
        self.spectrum_count += 1
        dock = QDockWidget(f"Spectrum {self.spectrum_count}", self)
        dock.setObjectName(f"spectrum_dock_{self.spectrum_count}")
        dock.setWidget(make_plot("m/z", "Intensity"))
        # Docks only hide when closed by default; free the memory instead.
        dock.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        return dock
