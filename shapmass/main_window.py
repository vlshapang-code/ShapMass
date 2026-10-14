"""The main application window.

Layout:
    - Center: the TIC plot (fixed in place).
    - Docks:  spectrum windows. Each one can be dragged around, docked to any
              edge, stacked into tabs, floated as its own window, or closed.

Interaction (see plot_widgets.py for zoom/pan controls):
    - Click the TIC         -> that scan is shown in the "Scan" window.
    - Click-drag on the TIC -> scans in that RT range are averaged into a new window.
"""
from pathlib import Path

# PySide6 must be imported before pyqtgraph so pyqtgraph picks the same Qt binding.
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QDockWidget, QFileDialog, QMainWindow, QMessageBox

import pyqtgraph as pg

from shapmass.plot_widgets import ChromatogramViewBox, TrackpadPlotWidget, TrackpadViewBox
from shapmass.raw_reader import RawFile

# White background / black axes, like most mass spec software.
pg.setConfigOptions(background="w", foreground="k", antialias=False)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ShapMass")
        self.resize(1200, 800)
        # Allow docks to be split side-by-side within the same edge.
        self.setDockNestingEnabled(True)

        self.raw = None           # The currently open RawFile
        self.scan_dock = None     # Spectrum window that shows the clicked scan

        tic_view = ChromatogramViewBox()
        tic_view.sigClicked.connect(self.show_scan_at)
        tic_view.sigRangeSelected.connect(self.average_range)
        self.tic_plot = TrackpadPlotWidget(tic_view, "Retention time (min)", "Intensity")
        self.tic_plot.setTitle("TIC")
        self.tic_curve = self.tic_plot.plot(pen=pg.mkPen("k", width=1))
        self.setCentralWidget(self.tic_plot)

        self._build_menus()
        self.scan_dock = self.add_spectrum_dock("Scan")
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

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Thermo RAW file", "", "Thermo RAW files (*.raw);;All files (*)"
        )
        if path:  # Empty string means the user cancelled
            self.open_file(Path(path))

    def open_file(self, path):
        try:
            raw = RawFile(path)
            rt, intensity, _scans = raw.tic()
        except Exception as err:
            QMessageBox.critical(self, "Could not open file", str(err))
            return

        if self.raw is not None:
            self.raw.close()
        self.raw = raw

        self.tic_curve.setData(rt, intensity)
        self.tic_plot.getViewBox().selection.hide()
        self.tic_plot.autoRange()
        self.setWindowTitle(f"ShapMass — {path.name}")
        self.statusBar().showMessage(
            f"{raw.instrument_model}  |  scans {raw.first_scan}–{raw.last_scan}  |  "
            f"RT {rt[0]:.2f}–{rt[-1]:.2f} min"
        )

    def closeEvent(self, event):
        if self.raw is not None:
            self.raw.close()
        super().closeEvent(event)

    # --- Spectra ---------------------------------------------------------

    def show_scan_at(self, retention_time):
        """Show the scan nearest retention_time in the Scan window."""
        if self.raw is None:
            return
        scan = self.raw.scan_number_at(retention_time)
        mz, intensity = self.raw.spectrum(scan)

        if self.scan_dock is None:  # The user closed it; make a new one
            self.scan_dock = self.add_spectrum_dock("Scan")
        rt = self.raw.retention_time(scan)
        self.scan_dock.setWindowTitle(f"Scan {scan}  ({rt:.2f} min)")
        self.plot_spectrum(self.scan_dock, mz, intensity, self.raw.scan_type(scan))

    def average_range(self, rt_start, rt_end):
        """Average the scans between two retention times into a new spectrum window."""
        if self.raw is None:
            return
        first = self.raw.scan_number_at(rt_start)
        last = self.raw.scan_number_at(rt_end)
        try:
            mz, intensity = self.raw.average(first, last)
        except Exception as err:
            self.statusBar().showMessage(f"Averaging failed: {err}")
            return

        start, end = self.raw.retention_time(first), self.raw.retention_time(last)
        dock = self.add_spectrum_dock(f"Average {start:.2f}–{end:.2f} min")
        self.plot_spectrum(dock, mz, intensity, self.raw.scan_type(first))

    def add_spectrum_dock(self, title):
        """Add a new, empty, movable spectrum window at the bottom."""
        plot = TrackpadPlotWidget(TrackpadViewBox(), "m/z", "Intensity")
        # Profile spectra have tens of thousands of points; only draw what's
        # visible, and downsample keeping peak maxima so no peak disappears.
        plot.setClipToView(True)
        plot.setDownsampling(auto=True, mode="peak")

        dock = QDockWidget(title, self)
        dock.setWidget(plot)
        # Docks only hide when closed by default; free the memory instead.
        dock.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dock.destroyed.connect(lambda: self._forget_dock(dock))
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        return dock

    def _forget_dock(self, dock):
        if dock is self.scan_dock:
            self.scan_dock = None

    def plot_spectrum(self, dock, mz, intensity, scan_type):
        plot = dock.widget()
        plot.clear()
        plot.plot(mz, intensity, pen=pg.mkPen("k", width=1))
        plot.setTitle(scan_type, size="9pt")
        plot.autoRange()
