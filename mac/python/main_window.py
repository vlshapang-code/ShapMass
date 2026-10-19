"""The main application window.

Layout:
    - Center: one TIC plot per open file, side by side (fixed in place).
    - Docks:  spectrum windows (SpectrumDock). Each one can be dragged around,
              docked to any edge, stacked into tabs, floated, or closed.

Interaction (see plot_widgets.py for zoom/pan controls):
    - Click the TIC          -> show that scan in the active spectrum window.
    - Click-drag on the TIC  -> average that RT range into the active spectrum window.
    - Hold Shift for either  -> put the result in a new spectrum window instead.
    - File > Add New Window  -> empty spectrum window that becomes the active one.
    - Drop .raw files on the window (or File > Open…) -> each file gets its own TIC, side
      by side in this same window. Spectra you pick from a TIC belong to that TIC's file.
    - Left/right arrows while hovering a plot -> step the active (or hovered) spectrum
      by one scan; averages slide by one scan, Shift+arrow grows/shrinks them.

The "active" window has a highlighted title bar; click inside a spectrum to make
it active. If there are no spectrum windows, a click or drag creates one.
"""
from pathlib import Path

# PySide6 must be imported before pyqtgraph so pyqtgraph picks the same Qt binding.
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox, QSplitter

import pyqtgraph as pg

from .plot_widgets import ChromatogramViewBox, TrackpadPlotWidget, with_controls
from .raw_reader import RawFile
from .spectrum_dock import SpectrumDock

# White background / black axes, like most mass spec software.
pg.setConfigOptions(background="w", foreground="k", antialias=False)


class Source:
    """One open file and the TIC plot that shows it."""

    def __init__(self):
        self.raw = None  # RawFile, or None until a file is loaded into this TIC
        self.view = ChromatogramViewBox()
        self.plot = TrackpadPlotWidget(self.view, "Retention time (min)", "Intensity")
        self.plot.setTitle("TIC")
        self.curve = self.plot.plot(pen=pg.mkPen("k", width=1))
        self.pane = with_controls(self.plot)  # what goes in the window: the plot plus its buttons


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setWindowTitle("ShapMass")
        self.resize(1200, 800)
        # Allow docks to be split side-by-side within the same edge.
        self.setDockNestingEnabled(True)

        self.sources = []         # Open files, left to right
        self.docks = []           # Open spectrum windows, oldest first
        self.active_dock = None   # The one plain TIC clicks/drags update
        self.dock_count = 0       # Used to number new spectrum windows

        # The TICs sit side by side; drag the dividers to resize them.
        self.tic_row = QSplitter(Qt.Orientation.Horizontal)
        self.tic_row.setChildrenCollapsible(False)
        self.setCentralWidget(self.tic_row)
        self._add_source()  # Starts empty; the first file opened fills it

        self._build_menus()
        self.statusBar().showMessage("File → Open… (or drop a .raw file on the window) to load a Thermo .raw file")

    def _add_source(self):
        """Add an empty TIC to the right of the existing ones."""
        source = Source()
        source.view.sigClicked.connect(lambda rt, new, s=source: self.show_scan_at(s, rt, new))
        source.view.sigRangeSelected.connect(lambda a, b, new, s=source: self.average_range(s, a, b, new))
        source.plot.sigStepRequested.connect(
            lambda step, shift: self.step_scans(self.active_dock, step, shift))
        self.sources.append(source)
        self.tic_row.addWidget(source.pane)
        self.tic_row.setSizes([1] * len(self.sources))  # equal widths
        return source

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
        """Show a file's TIC: in the empty one if there is one, otherwise in a new one beside the rest."""
        try:
            raw = RawFile(path)
            rt, intensity, _scans = raw.tic()
        except Exception as err:
            QMessageBox.critical(self, "Could not open file", str(err))
            return

        source = next((s for s in self.sources if s.raw is None), None) or self._add_source()
        source.raw = raw
        source.plot.setTitle(f"TIC — {path.name}")
        source.curve.setData(rt, intensity)
        source.view.mark(None)
        source.view.show_all()
        self.setWindowTitle(f"ShapMass — {path.name}" if len(self.sources) == 1 else "ShapMass")
        self.statusBar().showMessage(
            f"{path.name}  |  {raw.instrument_model}  |  scans {raw.first_scan}–{raw.last_scan}  |  "
            f"RT {rt[0]:.2f}–{rt[-1]:.2f} min"
        )

    def closeEvent(self, event):
        for source in self.sources:
            if source.raw is not None:
                source.raw.close()
        super().closeEvent(event)

    # --- Drag and drop ---------------------------------------------------

    @staticmethod
    def _raw_paths(mime):
        """The .raw files in a drag, in the order they were dragged."""
        return [Path(url.toLocalFile()) for url in mime.urls()
                if url.isLocalFile() and url.toLocalFile().lower().endswith(".raw")]

    def dragEnterEvent(self, event):
        if self._raw_paths(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        event.acceptProposedAction()
        for path in self._raw_paths(event.mimeData()):
            self.open_file(path)

    # --- Spectra ---------------------------------------------------------

    def show_scan_at(self, source, retention_time, new_window):
        """Show the scan nearest retention_time."""
        if source.raw is None:
            return
        scan = source.raw.scan_number_at(retention_time)
        self._show_scans(self._target_dock(source, new_window), scan, scan)

    def average_range(self, source, rt_start, rt_end, new_window):
        """Average the scans between two retention times."""
        if source.raw is None:
            return
        first = source.raw.scan_number_at(rt_start)
        # End on a scan of the same type as the first, so both ends are comparable.
        last = source.raw.snap_to_type(source.raw.scan_number_at(rt_end), first)
        self._show_scans(self._target_dock(source, new_window), first, last)

    def step_scans(self, dock, step, extend):
        """Left/right arrows over a plot.

        Single scan:   moves to the previous/next scan of the same type.
        Average:       slides the whole range by one scan.
        Shift+arrow:   on an average, moves only the right edge: grows or shrinks it by one scan.
        """
        if dock is None or dock.source is None or dock.scans is None:
            return
        raw = dock.source.raw
        first, last = dock.scans
        if extend:
            new_first, new_last = first, max(first, raw.step_scan(last, step))
            if new_last == last:
                return
        else:
            new_first = raw.step_scan(first, step)
            new_last = raw.step_scan(last, step)
            if new_first == first or new_last == last:  # hit the start or end of the file
                return
        self.set_active_dock(dock)
        self._show_scans(dock, new_first, new_last, keep_view=True)

    def _show_scans(self, dock, first, last, keep_view=False):
        """Draw scan `first` (if first == last) or the average of first..last in a dock."""
        raw = dock.source.raw
        try:
            if first == last:
                mz, intensity = raw.spectrum(first)
            else:
                mz, intensity = raw.average(first, last)
        except Exception as err:
            self.statusBar().showMessage(f"Could not read scans {first}–{last}: {err}")
            return

        start, end = raw.retention_time(first), raw.retention_time(last)
        if first == last:
            title = f"Scan {first}  ({start:.2f} min)"
        else:
            title = f"Average scans {first}–{last}  ({start:.2f}–{end:.2f} min)"
        dock.show_spectrum(
            title, mz, intensity, raw.scan_type(first), (first, last), (start, end), keep_view
        )
        self._update_tic_selection()

    def _target_dock(self, source, new_window):
        """The spectrum window a click/drag on source's TIC should draw into.

        The active window is reused only if it already shows this file (or is
        still empty); otherwise a click on another file's TIC starts a new one.
        """
        dock = self.active_dock
        if new_window or dock is None or dock.source not in (None, source):
            dock = self.new_spectrum_dock()
        dock.source = source
        return dock

    def new_spectrum_dock(self):
        """Add an empty spectrum window at the bottom and make it active."""
        self.dock_count += 1
        dock = SpectrumDock(f"Spectrum {self.dock_count}", self)
        dock.sigActivated.connect(self.set_active_dock)
        dock.sigClosed.connect(self._dock_closed)
        dock.sigStepRequested.connect(self.step_scans)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        self.docks.append(dock)
        self.set_active_dock(dock)
        # The new dock joins Qt's layout on the next event-loop tick; even out then.
        QTimer.singleShot(0, self._even_out_docks)
        return dock

    def _even_out_docks(self):
        """Give the docked spectrum windows equal widths, side by side."""
        docked = [d for d in self.docks if not d.isFloating() and not d.isHidden()
                  and self.dockWidgetArea(d) == Qt.DockWidgetArea.BottomDockWidgetArea]
        if len(docked) > 1:
            self.resizeDocks(docked, [self.width() // len(docked)] * len(docked),
                             Qt.Orientation.Horizontal)

    def set_active_dock(self, dock):
        if self.active_dock is not None and self.active_dock is not dock:
            self.active_dock.set_active(False)
        self.active_dock = dock
        dock.set_active(True)
        self._update_tic_selection()

    def _dock_closed(self, dock):
        self.docks.remove(dock)
        # Qt removes the closed window from the layout a moment later; re-even then.
        QTimer.singleShot(0, self._even_out_docks)
        if dock is self.active_dock:
            self.active_dock = None
            if self.docks:  # Hand "active" to the newest remaining window
                self.set_active_dock(self.docks[-1])
            else:
                self._update_tic_selection()

    def _update_tic_selection(self):
        """Mark on each TIC what the active spectrum window is showing (only on its own file's TIC)."""
        dock = self.active_dock
        for source in self.sources:
            shown = dock is not None and dock.source is source
            source.view.mark(dock.rt_range if shown else None)
