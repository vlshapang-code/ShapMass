"""Plot widgets with trackpad-friendly mouse controls.

All plots:
    pinch                       zoom in/out around the cursor
    two-finger scroll           pan
    double-click                zoom all the way out (all the data, intensity from zero)
    click-hold on an axis       drag up = zoom in on that axis, drag down = zoom out
    hover on the X axis         left/right arrows = pan the view along m/z or time
    hover on the Y axis         up/down arrows = pan the view along intensity
    hover on the plot area      left/right arrow = step scans   -> sigStepRequested(step, shift)
    right-click                 pyqtgraph's menu (export, etc.)

Spectrum plots (SpectrumViewBox):
    click                       zoom in on m/z around the click, intensity fits what is visible
    click-drag                  zoom to the m/z range you drag across (the spectrum never pans)

Whenever the view is fitted (clicks, drags, double-click, new data) intensity runs
from just below zero to just above the tallest peak in view, so peaks always rise
from the bottom of the plot.

Chromatogram plot:
    click                       pick a single scan        -> sigClicked(rt, shift)
    click-drag                  pick a range to average   -> sigRangeSelected(rt_start, rt_end, shift)
    (shift is True when the Shift key was held.)
"""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QVBoxLayout, QWidget

import numpy as np
import pyqtgraph as pg

LEFT = Qt.MouseButton.LeftButton
SHIFT = Qt.KeyboardModifier.ShiftModifier
SELECTION_COLOR = (80, 140, 255)

# How fast dragging on an axis zooms: each pixel of drag scales by this factor.
# 1.01 ** 70 ≈ 2, so a 70-pixel drag doubles/halves the zoom.
AXIS_ZOOM_PER_PIXEL = 1.01

# A click on a spectrum zooms the m/z axis in by this factor, centered on the click.
CLICK_ZOOM_FACTOR = 0.5

# Each arrow-key press pans the hovered axis by this fraction of the visible range
# (holding the key repeats it). 0.01 turns a 200-300 view into 201-301.
ARROW_PAN_FRACTION = 0.01

# A normal mouse wheel reports angles, not pixels. Pixels to pan per wheel notch.
WHEEL_NOTCH_PIXELS = 30

# Drags narrower than this (pixels) are ignored as accidental.
MIN_DRAG_PIXELS = 4

# Fitted views: intensity runs from -3% to +5% of the tallest peak in view, and
# "zoom all the way out" adds this fraction of the data's width on each side.
Y_BELOW_ZERO = 0.03
Y_ABOVE_TOP = 0.05
FULL_VIEW_MARGIN = 0.04


def make_band():
    """A shaded vertical band (a range on the x axis), hidden until used."""
    band = pg.LinearRegionItem(movable=False, brush=SELECTION_COLOR + (60,), pen=pg.mkPen(SELECTION_COLOR))
    band.hide()
    return band


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

        self.zoom(factor, self._anchor)

    def zoom(self, factor, anchor):
        """Scale this axis by factor (< 1 zooms in) around the data point anchor."""
        view = self.linkedView()
        if self.orientation in ("bottom", "top"):
            view.scaleBy(x=factor, center=anchor)
        else:
            # Intensity axis: keep the bottom edge fixed so the baseline stays put.
            bottom = view.viewRange()[1][0]
            view.scaleBy(y=factor, center=pg.Point(anchor.x(), bottom))

    def pan(self, fraction):
        """Slide the view along this axis by a fraction of the visible range (+ = up/right)."""
        view = self.linkedView()
        x_range, y_range = view.viewRange()
        if self.orientation in ("bottom", "top"):
            view.translateBy(x=fraction * (x_range[1] - x_range[0]))
        else:
            view.translateBy(y=fraction * (y_range[1] - y_range[0]))

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
            self.show_all()
        else:
            super().mouseClickEvent(ev)  # right-click menu

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() == LEFT:
            super().mouseDragEvent(ev, axis)  # pan
        else:
            ev.ignore()  # disable pyqtgraph's right-drag axis stretching

    def _data(self):
        """(x, y) arrays of the plotted curve, or None.

        Read from the item's own data: childrenBounds() would only report the
        slice currently in view, because spectra clip to the view.
        """
        for item in self.addedItems:
            x = getattr(item, "xData", None)
            if x is not None and len(x):
                return x, item.yData
        return None

    def fit_y(self, lo, hi, margin=0.0):
        """Show x from lo to hi (plus margin on each side), intensity fitted to the data in lo..hi."""
        data = self._data()
        if data is None:
            return
        x, y = data
        start, end = np.searchsorted(x, lo, "left"), np.searchsorted(x, hi, "right")
        top = y[start:end].max(initial=0.0)
        if top <= 0:
            top = 1.0
        self.setRange(xRange=(lo - margin, hi + margin),
                      yRange=(-Y_BELOW_ZERO * top, (1 + Y_ABOVE_TOP) * top), padding=0)

    def refit_y(self):
        """Keep the current x view but re-fit intensity to the (new) data in it."""
        self.fit_y(*self.viewRange()[0])

    def show_all(self):
        """Zoom all the way out: all the data with a small margin, intensity from zero."""
        data = self._data()
        if data is None:
            return
        lo, hi = data[0][0], data[0][-1]
        self.fit_y(lo, hi, margin=FULL_VIEW_MARGIN * max(hi - lo, np.finfo(float).eps))


class SpectrumViewBox(TrackpadViewBox):
    """Spectrum plot area: click to zoom in, drag to zoom to a range, double-click to zoom out.

    Dragging never pans the spectrum.
    """

    def __init__(self):
        super().__init__()
        # Shaded band shown while dragging out a range to zoom to.
        self.zoom_band = make_band()
        self.addItem(self.zoom_band, ignoreBounds=True)

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() != LEFT:
            ev.ignore()
            return
        ev.accept()
        start = self.mapSceneToView(ev.buttonDownScenePos()).x()
        end = self.mapSceneToView(ev.scenePos()).x()
        lo, hi = sorted((start, end))
        if not ev.isFinish():
            self.zoom_band.setRegion((lo, hi))
            self.zoom_band.show()
            return
        self.zoom_band.hide()
        if hi - lo > MIN_DRAG_PIXELS * self.viewPixelSize()[0]:
            self.fit_y(lo, hi)

    def mouseClickEvent(self, ev):
        if ev.button() != LEFT:
            super().mouseClickEvent(ev)  # right-click menu
            return
        ev.accept()
        if ev.double():
            self.show_all()  # (the click before it had already zoomed in; this overrides it)
        else:
            self._zoom_x(self.mapSceneToView(ev.scenePos()).x(), CLICK_ZOOM_FACTOR)

    def _zoom_x(self, center, factor):
        """Scale the m/z view by factor around center; fit intensity to what is visible."""
        lo, hi = self.viewRange()[0]
        self.fit_y(center - (center - lo) * factor, center + (hi - center) * factor)


class ChromatogramViewBox(TrackpadViewBox):
    """Like TrackpadViewBox, but click picks a scan and click-drag picks an RT range."""

    sigClicked = Signal(float, bool)               # retention time, shift held
    sigRangeSelected = Signal(float, float, bool)  # start, end retention time, shift held

    def __init__(self):
        super().__init__()
        # Shaded band marking an averaged range, or a line marking a single scan.
        self.selection = make_band()
        self.marker = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(SELECTION_COLOR, width=2))
        self.marker.hide()
        for item in (self.selection, self.marker):
            self.addItem(item, ignoreBounds=True)  # don't let them affect auto-zoom
        self.marked = None  # the (start, end) RT range last passed to mark()

    def mark(self, rt_range):
        """Mark an RT range: a band for an average, a line for a single scan (start == end), or nothing (None)."""
        self.marked = rt_range
        self._draw(rt_range)

    def _draw(self, rt_range):
        single = rt_range is not None and rt_range[0] == rt_range[1]
        self.selection.setVisible(rt_range is not None and not single)
        self.marker.setVisible(single)
        if single:
            self.marker.setValue(rt_range[0])
        elif rt_range is not None:
            self.selection.setRegion(rt_range)

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
        if not ev.isFinish():
            self._draw((lo, hi))
            return
        self._draw(self.marked)  # whoever handles the signal marks the new range
        if hi - lo > MIN_DRAG_PIXELS * self.viewPixelSize()[0]:  # ignore accidental tiny drags
            self.sigRangeSelected.emit(lo, hi, bool(ev.modifiers() & SHIFT))


class TrackpadPlotWidget(pg.PlotWidget):
    """A PlotWidget wired up with ZoomAxis, a trackpad ViewBox and pinch-to-zoom."""

    sigPressed = Signal()  # any mouse press inside the plot (used to pick the active window)
    sigStepRequested = Signal(int, bool)  # left/right arrow in the plot area: step (-1/+1), shift held

    def __init__(self, view_box, x_label, y_label):
        super().__init__(
            viewBox=view_box,
            axisItems={"bottom": ZoomAxis("bottom"), "left": ZoomAxis("left")},
        )
        self.setLabel("bottom", x_label)
        self.setLabel("left", y_label)
        self.showGrid(x=True, y=True, alpha=0.2)
        # Let file drops fall through to the main window.
        self.setAcceptDrops(False)

    def mousePressEvent(self, ev):
        self.sigPressed.emit()
        super().mousePressEvent(ev)

    def enterEvent(self, ev):
        # Take keyboard focus on hover so arrow keys work without clicking first.
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().enterEvent(ev)

    def _axis_under_cursor(self):
        """The axis the mouse is hovering over, or None.

        Uses the axis's layout geometry rather than its bounding rect, which is
        stretched across the whole plot by the grid lines.
        """
        scene_pos = self.mapToScene(self.viewport().mapFromGlobal(QCursor.pos()))
        for name in ("bottom", "left"):
            axis = self.getPlotItem().getAxis(name)
            if axis.isVisible() and axis.parentItem().mapRectToScene(axis.geometry()).contains(scene_pos):
                return axis, scene_pos
        return None, scene_pos

    def keyPressEvent(self, ev):
        """Arrow keys pan the axis under the mouse.

        X axis:  right = view moves toward larger values, left = smaller
        Y axis:  up = larger values, down = smaller
        Over the plot area itself, left/right step scans (see below).
        """
        axis, _scene_pos = self._axis_under_cursor()
        key = ev.key()
        if axis is not None:
            vertical = axis.orientation in ("left", "right")
            increase = Qt.Key.Key_Up if vertical else Qt.Key.Key_Right
            decrease = Qt.Key.Key_Down if vertical else Qt.Key.Key_Left
            if key in (increase, decrease):
                axis.pan(ARROW_PAN_FRACTION if key == increase else -ARROW_PAN_FRACTION)
                ev.accept()
                return
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            # Over the plot area itself (not an axis): step through scans.
            cursor = self.viewport().mapFromGlobal(QCursor.pos())
            if self.viewport().rect().contains(cursor):
                step = 1 if key == Qt.Key.Key_Right else -1
                self.sigStepRequested.emit(step, bool(ev.modifiers() & SHIFT))
                ev.accept()
                return
        super().keyPressEvent(ev)

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
                view.show_all()  # two-finger double-tap
                return True
        return super().viewportEvent(event)


# Pane controls: (symbol on the button, tooltip). Not wired up yet.
CONTROLS = [
    ("⧉", "Duplicate"),
    ("⊘", "Hide"),
    ("◉", "Hide all others"),
    ("⊟", "Background subtract"),
    ("🔗", "Link"),
    ("◪", "Overlay"),
]

# Button bar matches the plot's white background, even in dark mode.
BUTTON_BAR_STYLE = "QWidget#buttonBar { background: white; } QToolButton { color: black; font-size: 16px; }"


def with_controls(plot):
    """Wrap a plot in a widget with a row of symbol buttons above it (TIC and spectrum panes)."""
    buttons = QHBoxLayout()
    buttons.setContentsMargins(4, 2, 4, 0)
    buttons.setSpacing(2)
    for symbol, tooltip in CONTROLS:
        button = QToolButton()
        button.setText(symbol)
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        buttons.addWidget(button)
    buttons.addStretch()

    body = QWidget()
    body.setObjectName("buttonBar")
    body.setStyleSheet(BUTTON_BAR_STYLE)
    layout = QVBoxLayout(body)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addLayout(buttons)
    layout.addWidget(plot)
    return body
