"""A movable spectrum window (a Qt dock widget holding one spectrum plot)."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDockWidget

import pyqtgraph as pg

from .plot_widgets import SpectrumViewBox, TrackpadPlotWidget, with_controls

# Title bar highlight for the active window (the one plain TIC clicks update).
ACTIVE_STYLE = "QDockWidget::title { background: rgb(190, 210, 255); }"

class SpectrumDock(QDockWidget):
    sigActivated = Signal(object)  # emits self when the user clicks inside the plot
    sigClosed = Signal(object)     # emits self the moment the window is closed
    sigStepRequested = Signal(object, int, bool)  # self, step (-1/+1), shift held

    def __init__(self, title, parent):
        super().__init__(title, parent)
        self.view = SpectrumViewBox()
        self.plot = TrackpadPlotWidget(self.view, "m/z", "Intensity")
        # Profile spectra have tens of thousands of points; only draw what's
        # visible, and downsample keeping peak maxima so no peak disappears.
        self.plot.setClipToView(True)
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.sigPressed.connect(lambda: self.sigActivated.emit(self))
        self.plot.sigStepRequested.connect(lambda step, shift: self.sigStepRequested.emit(self, step, shift))

        self.setWidget(with_controls(self.plot))
        # Docks only hide when closed by default; free the memory instead.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        # What is shown: scan numbers (first, last) and the matching retention-time
        # range. first == last for a single scan.
        self.scans = None
        self.rt_range = None
        self.source = None  # The open file this window's spectra come from

    def show_spectrum(self, title, mz, intensity, scan_type, scans, rt_range, keep_view=False):
        """Draw a spectrum. keep_view=True keeps the m/z zoom and only re-fits intensity (used when stepping)."""
        self.setWindowTitle(title)
        self.scans = scans
        self.rt_range = rt_range
        self.plot.clear()
        self.plot.plot(mz, intensity, pen=pg.mkPen("k", width=1))
        self.plot.setTitle(scan_type, size="9pt")
        if keep_view:
            self.view.refit_y()
        else:
            self.view.show_all()

    def set_active(self, active):
        self.setStyleSheet(ACTIVE_STYLE if active else "")

    def closeEvent(self, event):
        # Qt deletes the widget a moment later; tell the main window right away.
        self.sigClosed.emit(self)
        super().closeEvent(event)
