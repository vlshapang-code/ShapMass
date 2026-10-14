"""The main application window.

Layout:
    - Center: the TIC plot (fixed in place).
    - Docks:  spectrum windows (SpectrumDock). Each one can be dragged around,
              docked to any edge, stacked into tabs, floated, or closed.

Interaction (see plot_widgets.py for zoom/pan controls):
    - Click the TIC          -> show that scan in the active spectrum window.
    - Click-drag on the TIC  -> average that RT range into the active spectrum window.
    - Hold Shift for either  -> put the result in a new spectrum window instead.
    - File > Add New Window  -> empty spectrum window that becomes the active one.

The "active" window has a highlighted title bar; click inside a spectrum to make
it active. If there are no spectrum windows, a click or drag creates one.
"""
from pathlib import Path

# PySide6 must be imported before pyqtgraph so pyqtgraph picks the same Qt binding.
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox

import pyqtgraph as pg

from shapmass.plot_widgets import ChromatogramViewBox, TrackpadPlotWidget
from shapmass.raw_reader import RawFile
from shapmass.spectrum_dock import SpectrumDock

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
        self.docks = []           # Open spectrum windows, oldest first
        self.active_dock = None   # The one plain TIC clicks/drags update
        self.dock_count = 0       # Used to number new spectrum windows

        self.tic_view = ChromatogramViewBox()
        self.tic_view.sigClicked.connect(self.show_scan_at)
        self.tic_view.sigRangeSelected.connect(self.average_range)
        self.tic_plot = TrackpadPlotWidget(self.tic_view, "Retention time (min)", "Intensity")
        self.tic_plot.setTitle("TIC")
        self.tic_curve = self.tic_plot.plot(pen=pg.mkPen("k", width=1))
        self.setCentralWidget(self.tic_plot)

        self._build_menus()
        self.statusBar().showMessage("File → Open… to load a Thermo .raw file")

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("&File")

        open_action = QAction("&Open…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)  # Cmd+O
        open_action.triggered.connect(self.open_file_dialog)
        file_menu.addAction(open_action)

        new_window_action = QAction("Add New Window", self)
        new_window_action.setShortcut(QKeySequence("Ctrl+N"))  # Cmd+N on macOS
        new_window_action.triggered.connect(self.new_spectrum_dock)
        file_menu.addAction(new_window_action)

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

        # A new file starts with just the TIC; old spectra belong to the old file.
        for dock in list(self.docks):
            dock.close()

        self.tic_curve.setData(rt, intensity)
        self.tic_view.clear_selection()
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

    def show_scan_at(self, retention_time, new_window):
        """Show the scan nearest retention_time."""
        if self.raw is None:
            return
        scan = self.raw.scan_number_at(retention_time)
        mz, intensity = self.raw.spectrum(scan)
        rt = self.raw.retention_time(scan)

        dock = self._target_dock(new_window)
        dock.show_spectrum(
            f"Scan {scan}  ({rt:.2f} min)", mz, intensity, self.raw.scan_type(scan), (rt, rt)
        )
        self._update_tic_selection()

    def average_range(self, rt_start, rt_end, new_window):
        """Average the scans between two retention times."""
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
        dock = self._target_dock(new_window)
        dock.show_spectrum(
            f"Average {start:.2f}–{end:.2f} min", mz, intensity, self.raw.scan_type(first),
            (start, end),
        )
        self._update_tic_selection()

    def _target_dock(self, new_window):
        """The spectrum window a TIC click/drag should draw into."""
        if new_window or self.active_dock is None:
            return self.new_spectrum_dock()
        return self.active_dock

    def new_spectrum_dock(self):
        """Add an empty spectrum window at the bottom and make it active."""
        self.dock_count += 1
        dock = SpectrumDock(f"Spectrum {self.dock_count}", self)
        dock.sigActivated.connect(self.set_active_dock)
        dock.sigClosed.connect(self._dock_closed)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        self.docks.append(dock)
        self.set_active_dock(dock)
        return dock

    def set_active_dock(self, dock):
        if self.active_dock is not None and self.active_dock is not dock:
            self.active_dock.set_active(False)
        self.active_dock = dock
        dock.set_active(True)
        self._update_tic_selection()

    def _dock_closed(self, dock):
        self.docks.remove(dock)
        if dock is self.active_dock:
            self.active_dock = None
            if self.docks:  # Hand "active" to the newest remaining window
                self.set_active_dock(self.docks[-1])
            else:
                self._update_tic_selection()

    def _update_tic_selection(self):
        """Mark on the TIC what the active spectrum window is showing."""
        rt_range = self.active_dock.rt_range if self.active_dock else None
        if rt_range is None:
            self.tic_view.clear_selection()
        elif rt_range[0] == rt_range[1]:
            self.tic_view.show_marker(rt_range[0])
        else:
            self.tic_view.show_range(*rt_range)
