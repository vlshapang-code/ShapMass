"""A movable spectrum window (a Qt dock widget holding one spectrum plot)."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDockWidget

import pyqtgraph as pg

from shapmass.plot_widgets import TrackpadPlotWidget, TrackpadViewBox

# Title bar highlight for the active window (the one plain TIC clicks update).
ACTIVE_STYLE = "QDockWidget::title { background: rgb(190, 210, 255); }"


class SpectrumDock(QDockWidget):
    sigActivated = Signal(object)  # emits self when the user clicks inside the plot
    sigClosed = Signal(object)     # emits self the moment the window is closed

    def __init__(self, title, parent):
        super().__init__(title, parent)
        self.plot = TrackpadPlotWidget(TrackpadViewBox(), "m/z", "Intensity")
        # Profile spectra have tens of thousands of points; only draw what's
        # visible, and downsample keeping peak maxima so no peak disappears.
        self.plot.setClipToView(True)
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.sigPressed.connect(lambda: self.sigActivated.emit(self))
        self.setWidget(self.plot)
        # Docks only hide when closed by default; free the memory instead.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        # Retention-time range shown, as (start, end); start == end for a single scan.
        self.rt_range = None

    def show_spectrum(self, title, mz, intensity, scan_type, rt_range):
        self.setWindowTitle(title)
        self.rt_range = rt_range
        self.plot.clear()
        self.plot.plot(mz, intensity, pen=pg.mkPen("k", width=1))
        self.plot.setTitle(scan_type, size="9pt")
        self.plot.autoRange()

    def set_active(self, active):
        self.setStyleSheet(ACTIVE_STYLE if active else "")

    def closeEvent(self, event):
        # Qt deletes the widget a moment later; tell the main window right away.
        self.sigClosed.emit(self)
        super().closeEvent(event)
