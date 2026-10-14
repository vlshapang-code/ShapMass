"""Plot widgets with trackpad-friendly mouse controls.

All plots:
    pinch                       zoom in/out around the cursor
    two-finger scroll           pan
    double-click                reset zoom to fit the data
    click-hold on an axis       drag up = zoom in on that axis, drag down = zoom out
    right-click                 pyqtgraph's menu (export, etc.)

Spectrum plots:
    click-drag                  pan

Chromatogram plot:
    click                       pick a single scan        -> sigClicked(rt, shift)
    click-drag                  pick a range to average   -> sigRangeSelected(rt_start, rt_end, shift)
    (shift is True when the Shift key was held.)
"""
from PySide6.QtCore import QEvent, Qt, Signal

import pyqtgraph as pg

LEFT = Qt.MouseButton.LeftButton
SHIFT = Qt.KeyboardModifier.ShiftModifier
SELECTION_COLOR = (80, 140, 255)

# How fast dragging on an axis zooms: each pixel of drag scales by this factor.
# 1.01 ** 70 ≈ 2, so a 70-pixel drag doubles/halves the zoom.
AXIS_ZOOM_PER_PIXEL = 1.01

# A normal mouse wheel reports angles, not pixels. Pixels to pan per wheel notch.
WHEEL_NOTCH_PIXELS = 30


class ZoomAxis(pg.AxisItem):
    """An axis you zoom by click-dragging on it: up zooms in, down zooms out."""

    def mouseDragEvent(self, ev):
        view = self.linkedView()
        if ev.button() != LEFT or view is None:
            ev.ignore()
            return
        ev.accept()

        if ev.isStart():
            # Zoom around the data value under the point where the drag began.
            self._anchor = view.mapSceneToView(ev.buttonDownScenePos())

        # Screen y grows downward, so dragging up gives dy < 0 -> factor < 1 -> zoom in.
        dy = ev.screenPos().y() - ev.lastScreenPos().y()
        factor = AXIS_ZOOM_PER_PIXEL ** dy

        if self.orientation in ("bottom", "top"):
            view.scaleBy(x=factor, center=self._anchor)
        else:
            # Intensity axis: keep the bottom edge fixed so the baseline stays put.
            bottom = view.viewRange()[1][0]
            view.scaleBy(y=factor, center=pg.Point(self._anchor.x(), bottom))

    def wheelEvent(self, ev):
        ev.ignore()  # Axis zoom is drag-only; don't let scrolling over an axis zoom it.


class TrackpadViewBox(pg.ViewBox):
    """The plotting area: scroll pans, double-click resets, left-drag pans."""

    def wheelEvent(self, ev, axis=None):
        delta = ev.pixelDelta()  # Trackpads report exact pixel movement
        if delta.isNull():       # Mouse wheels don't; convert notches to pixels
            notches = ev.delta() / 120
            if ev.orientation() == Qt.Orientation.Horizontal:
                dx, dy = notches * WHEEL_NOTCH_PIXELS, 0
            else:
                dx, dy = 0, notches * WHEEL_NOTCH_PIXELS
        else:
            dx, dy = delta.x(), delta.y()

        pixel_w, pixel_h = self.viewPixelSize()  # data units per screen pixel
        self.translateBy(x=-dx * pixel_w, y=dy * pixel_h)
        ev.accept()

    def mouseClickEvent(self, ev):
        if ev.button() == LEFT and ev.double():
            ev.accept()
            self.autoRange()
        else:
            super().mouseClickEvent(ev)  # right-click menu

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() == LEFT:
            super().mouseDragEvent(ev, axis)  # pan
        else:
            ev.ignore()  # disable pyqtgraph's right-drag axis stretching


class ChromatogramViewBox(TrackpadViewBox):
    """Like TrackpadViewBox, but click picks a scan and click-drag picks an RT range."""

    sigClicked = Signal(float, bool)               # retention time, shift held
    sigRangeSelected = Signal(float, float, bool)  # start, end retention time, shift held

    def __init__(self):
        super().__init__()
        # Shaded band marking an averaged range, or a line marking a single scan.
        self.selection = pg.LinearRegionItem(
            movable=False, brush=SELECTION_COLOR + (60,), pen=pg.mkPen(SELECTION_COLOR)
        )
        self.marker = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(SELECTION_COLOR, width=2))
        for item in (self.selection, self.marker):
            item.hide()
            self.addItem(item, ignoreBounds=True)  # don't let them affect auto-zoom

    def show_range(self, start, end):
        self.marker.hide()
        self.selection.setRegion((start, end))
        self.selection.show()

    def show_marker(self, retention_time):
        self.selection.hide()
        self.marker.setValue(retention_time)
        self.marker.show()

    def clear_selection(self):
        self.selection.hide()
        self.marker.hide()

    def mouseClickEvent(self, ev):
        if ev.button() == LEFT and not ev.double():
            ev.accept()
            shift = bool(ev.modifiers() & SHIFT)
            self.sigClicked.emit(self.mapSceneToView(ev.scenePos()).x(), shift)
        else:
            super().mouseClickEvent(ev)

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() != LEFT:
            ev.ignore()
            return
        ev.accept()
        start = self.mapSceneToView(ev.buttonDownScenePos()).x()
        end = self.mapSceneToView(ev.scenePos()).x()
        lo, hi = sorted((start, end))
        self.show_range(lo, hi)
        if ev.isFinish():
            self.sigRangeSelected.emit(lo, hi, bool(ev.modifiers() & SHIFT))


class TrackpadPlotWidget(pg.PlotWidget):
    """A PlotWidget wired up with ZoomAxis, a trackpad ViewBox and pinch-to-zoom."""

    sigPressed = Signal()  # any mouse press inside the plot (used to pick the active window)

    def __init__(self, view_box, x_label, y_label):
        super().__init__(
            viewBox=view_box,
            axisItems={"bottom": ZoomAxis("bottom"), "left": ZoomAxis("left")},
        )
        self.setLabel("bottom", x_label)
        self.setLabel("left", y_label)
        self.showGrid(x=True, y=True, alpha=0.2)

    def mousePressEvent(self, ev):
        self.sigPressed.emit()
        super().mousePressEvent(ev)

    def viewportEvent(self, event):
        # macOS trackpad pinches arrive as "native gestures", not wheel events.
        if event.type() == QEvent.Type.NativeGesture:
            view = self.getViewBox()
            scene_pos = self.mapToScene(event.position().toPoint())
            gesture = event.gestureType()

            if gesture == Qt.NativeGestureType.ZoomNativeGesture:
                if view.sceneBoundingRect().contains(scene_pos):
                    # value() > 0 for pinch-out (zoom in); scaleBy < 1 zooms in.
                    factor = 1 / (1 + event.value())
                    view.scaleBy(s=(factor, factor), center=view.mapSceneToView(scene_pos))
                return True
            if gesture == Qt.NativeGestureType.SmartZoomNativeGesture:
                view.autoRange()  # two-finger double-tap
                return True
        return super().viewportEvent(event)
