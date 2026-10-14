//! Plot behaviour shared by the TIC and spectra. Rust twin of `python/plot_widgets.py`.
//!
//! All plots:
//!     pinch / Ctrl+scroll wheel   zoom in/out around the cursor        (egui_plot built-in;
//!                                 Windows sends touchpad pinches as Ctrl+scroll)
//!     two-finger scroll / wheel   pan, Shift+wheel pans sideways       (egui_plot built-in)
//!     click-hold on an axis       drag up = zoom in on that axis, drag down = zoom out
//!     hover on the X axis         left/right arrows = pan along m/z or time
//!     hover on the Y axis         up/down arrows = pan along intensity
//!     hover on the plot area      left/right arrows = step scans   -> PlotEvents::step
//!     double-click                zoom all the way out
//!
//! Spectrum plots (Kind::Spectrum):
//!     click                       zoom in on m/z around the click, intensity fits what is visible
//!     click-drag                  zoom to the m/z range you drag across (never pans)
//!
//! Chromatogram plot (Kind::Chromatogram):
//!     click                       pick a single scan                -> PlotEvents::clicked
//!     click-drag                  pick a range to average           -> PlotEvents::drag_finished
//!
//! egui redraws every frame ("immediate mode"), so instead of moving the view
//! directly we store the new bounds in `PlotState::pending` and apply them on
//! the next frame.

use eframe::egui::{self, Color32, Key, PointerButton, Rect, Sense, Stroke, Ui, pos2};
use egui_plot::{Line, Plot, PlotBounds};

/// Each pixel of drag on an axis scales by this factor (1.01^70 ≈ 2).
const AXIS_ZOOM_PER_PIXEL: f64 = 1.01;
/// Each arrow-key press pans the hovered axis by this fraction of the visible range.
const ARROW_PAN_FRACTION: f64 = 0.01;
/// A click on a spectrum zooms the m/z axis in by this factor, centered on the click.
const CLICK_ZOOM_FACTOR: f64 = 0.5;
/// Drags narrower than this (pixels) are ignored as accidental.
const MIN_DRAG_PIXELS: f32 = 4.0;

const SELECTION: Color32 = Color32::from_rgb(80, 140, 255);
const SELECTION_FILL: Color32 = Color32::from_rgba_premultiplied(20, 35, 64, 60);

#[derive(Clone, Copy, PartialEq)]
pub enum Kind {
    Chromatogram,
    Spectrum,
}

/// What to mark on the plot (the TIC shows what the active spectrum window shows).
pub enum Marker {
    None,
    Line(f64),
    Band(f64, f64),
}

/// Per-plot memory that survives between frames.
#[derive(Default)]
pub struct PlotState {
    pending: Option<PlotBounds>, // bounds to apply on the next frame
    last_bounds: Option<PlotBounds>,
    drag_start: Option<f64>,  // x value where a plot-area drag began
    axis_anchor: Option<f64>, // value where an axis drag began
    initialized: bool,
}

impl PlotState {
    /// Show all the data on the next frame (used when new data replaces old).
    pub fn reset_view(&mut self) {
        self.initialized = false;
    }

    /// Keep the current m/z view but re-fit intensity to the (new) data in it.
    pub fn refit_y(&mut self, x: &[f64], y: &[f64]) {
        if let Some(b) = self.last_bounds {
            self.pending = Some(fit_y(b.min()[0], b.max()[0], x, y));
        }
    }
}

/// What the user did to the plot this frame.
#[derive(Default)]
pub struct PlotEvents {
    pub clicked: Option<f64>,               // chromatogram: x value clicked
    pub drag_finished: Option<(f64, f64)>,  // chromatogram: x range dragged
    pub step: Option<i64>,                  // left/right arrow over the plot area
    pub shift: bool,                        // Shift held
    pub pressed: bool,                      // mouse pressed inside the plot
}

#[allow(clippy::too_many_arguments)]
pub fn show(
    ui: &mut Ui,
    id: egui::Id,
    state: &mut PlotState,
    kind: Kind,
    x: &[f64],
    y: &[f64],
    labels: (&str, &str),
    marker: Marker,
) -> PlotEvents {
    let mut ev = PlotEvents { shift: ui.input(|i| i.modifiers.shift), ..Default::default() };
    let full_rect = ui.available_rect_before_wrap(); // plot + its axes

    if !state.initialized && !x.is_empty() {
        state.pending = Some(full_bounds(x, y));
        state.initialized = true;
    }
    let pending = state.pending.take();

    let plot = Plot::new(id)
        .x_axis_label(labels.0)
        .y_axis_label(labels.1)
        .allow_drag(false) // we handle click-drag ourselves
        .allow_boxed_zoom(false)
        .allow_axis_zoom_drag(false) // egui_plot's version zooms sideways; ours is up/down
        .allow_double_click_reset(false) // ours ignores padding from clipped data
        .auto_bounds(false);

    let response = plot.show(ui, |plot_ui| {
        if let Some(bounds) = pending {
            plot_ui.set_plot_bounds(bounds);
        }
        let bounds = plot_ui.plot_bounds();
        let pixels = plot_ui.transform().frame().width().max(1.0) as usize;
        let points = decimate(x, y, bounds.min()[0], bounds.max()[0], pixels);
        plot_ui.line(Line::new("", points).color(Color32::BLACK).width(1.0));
    });

    let t = response.transform;
    let frame = *t.frame();
    let bounds = *t.bounds();
    state.last_bounds = Some(bounds);
    let r = &response.response;
    let pointer = r.interact_pointer_pos().or(ui.input(|i| i.pointer.latest_pos()));
    ev.pressed = r.is_pointer_button_down_on();

    // --- Clicks ---------------------------------------------------------
    if r.double_clicked() {
        state.pending = Some(full_bounds(x, y));
    } else if r.clicked() {
        if let Some(p) = pointer {
            let value = t.value_from_position(p).x;
            match kind {
                Kind::Chromatogram => ev.clicked = Some(value),
                Kind::Spectrum => state.pending = Some(zoom_x(&bounds, value, CLICK_ZOOM_FACTOR, x, y)),
            }
        }
    }

    // --- Click-drag in the plot area ---------------------------------------
    let mut band = None;
    if r.drag_started_by(PointerButton::Primary) {
        state.drag_start = pointer.map(|p| t.value_from_position(p).x);
    }
    if let (Some(start), Some(p)) = (state.drag_start, pointer) {
        let end = t.value_from_position(p).x;
        let (lo, hi) = (start.min(end), start.max(end));
        band = Some((lo, hi));
        if r.drag_stopped() {
            state.drag_start = None;
            band = None;
            let width = (t.position_from_point_x(hi) - t.position_from_point_x(lo)).abs();
            if width > MIN_DRAG_PIXELS {
                match kind {
                    Kind::Chromatogram => ev.drag_finished = Some((lo, hi)),
                    Kind::Spectrum => state.pending = Some(fit_y(lo, hi, x, y)),
                }
            }
        }
    }

    // --- Axes: drag up/down to zoom, arrow keys to pan ------------------------
    let x_axis = Rect::from_min_max(pos2(frame.left(), frame.bottom()), pos2(frame.right(), full_rect.bottom()));
    let y_axis = Rect::from_min_max(pos2(full_rect.left(), frame.top()), pos2(frame.left(), frame.bottom()));
    for (rect, is_x) in [(x_axis, true), (y_axis, false)] {
        let axis = ui.interact(rect, id.with(is_x), Sense::drag()).on_hover_cursor(egui::CursorIcon::ResizeVertical);
        if axis.drag_started() {
            state.axis_anchor = axis.interact_pointer_pos().map(|p| {
                // X: zoom around the value under the mouse. Y: keep the baseline fixed.
                if is_x { t.value_from_position(p).x } else { bounds.min()[1] }
            });
        }
        let dy = axis.drag_delta().y as f64;
        if axis.dragged() && dy != 0.0 && let Some(anchor) = state.axis_anchor {
            // Screen y grows downward, so dragging up gives dy < 0 -> factor < 1 -> zoom in.
            let b = state.pending.unwrap_or(bounds);
            state.pending = Some(scale_axis(&b, is_x, anchor, AXIS_ZOOM_PER_PIXEL.powf(dy)));
        }
    }

    let hover = ui.input(|i| i.pointer.hover_pos());
    let keys = ui.input(|i| {
        [Key::ArrowLeft, Key::ArrowRight, Key::ArrowUp, Key::ArrowDown].map(|k| i.key_pressed(k))
    });
    let [left, right, up, down] = keys;
    if let Some(h) = hover {
        let b = state.pending.unwrap_or(bounds);
        if x_axis.contains(h) && (left || right) {
            let shift = if right { ARROW_PAN_FRACTION } else { -ARROW_PAN_FRACTION } * b.width();
            state.pending = Some(PlotBounds::from_min_max(
                [b.min()[0] + shift, b.min()[1]], [b.max()[0] + shift, b.max()[1]]));
        } else if y_axis.contains(h) && (up || down) {
            let shift = if up { ARROW_PAN_FRACTION } else { -ARROW_PAN_FRACTION } * b.height();
            state.pending = Some(PlotBounds::from_min_max(
                [b.min()[0], b.min()[1] + shift], [b.max()[0], b.max()[1] + shift]));
        } else if frame.contains(h) && (left || right) {
            ev.step = Some(if right { 1 } else { -1 });
        }
    }

    // --- Overlays ----------------------------------------------------------------
    let painter = ui.painter_at(frame);
    let draw_band = |lo: f64, hi: f64| {
        let (x0, x1) = (t.position_from_point_x(lo), t.position_from_point_x(hi));
        painter.rect_filled(Rect::from_x_y_ranges(x0..=x1, frame.y_range()), 0.0, SELECTION_FILL);
        for x in [x0, x1] {
            painter.vline(x, frame.y_range(), Stroke::new(1.0, SELECTION));
        }
    };
    match (band, marker) {
        (Some((lo, hi)), _) | (None, Marker::Band(lo, hi)) => draw_band(lo, hi),
        (None, Marker::Line(v)) => {
            painter.vline(t.position_from_point_x(v), frame.y_range(), Stroke::new(2.0, SELECTION));
        }
        (None, Marker::None) => {}
    }

    if state.pending.is_some() {
        ui.ctx().request_repaint(); // apply the new bounds right away
    }
    ev
}

/// Peak-preserving downsampling: keep only points inside [lo, hi] and, if
/// there are many more than pixels, the min and max of each pixel column.
fn decimate(x: &[f64], y: &[f64], lo: f64, hi: f64, pixels: usize) -> Vec<[f64; 2]> {
    let start = x.partition_point(|&v| v < lo).saturating_sub(1);
    let end = (x.partition_point(|&v| v <= hi) + 1).min(x.len());
    if start >= end {
        return Vec::new();
    }
    if end - start <= 4 * pixels {
        return (start..end).map(|i| [x[i], y[i]]).collect();
    }
    let mut out = Vec::with_capacity(4 * pixels);
    let bucket = (end - start).div_ceil(pixels);
    for chunk_start in (start..end).step_by(bucket) {
        let chunk = chunk_start..(chunk_start + bucket).min(end);
        let (mut i_min, mut i_max) = (chunk.start, chunk.start);
        for i in chunk {
            if y[i] < y[i_min] { i_min = i; }
            if y[i] > y[i_max] { i_max = i; }
        }
        let (a, b) = if i_min < i_max { (i_min, i_max) } else { (i_max, i_min) };
        out.push([x[a], y[a]]);
        out.push([x[b], y[b]]);
    }
    out
}

/// Bounds showing x in [lo, hi] with intensity fitted to the data inside it.
fn fit_y(lo: f64, hi: f64, x: &[f64], y: &[f64]) -> PlotBounds {
    let start = x.partition_point(|&v| v < lo);
    let end = x.partition_point(|&v| v <= hi);
    let top = y[start..end.max(start)].iter().cloned().fold(0.0, f64::max);
    let top = if top > 0.0 { top } else { 1.0 };
    PlotBounds::from_min_max([lo, -0.03 * top], [hi, 1.05 * top])
}

/// Everything, with a small margin (the "fully zoomed out" view).
fn full_bounds(x: &[f64], y: &[f64]) -> PlotBounds {
    let (lo, hi) = (x[0], x[x.len() - 1]);
    let margin = 0.04 * (hi - lo).max(f64::EPSILON);
    let fitted = fit_y(lo, hi, x, y);
    PlotBounds::from_min_max([lo - margin, fitted.min()[1]], [hi + margin, fitted.max()[1]])
}

/// Scale the x range by factor around center, fitting intensity to what is visible.
fn zoom_x(b: &PlotBounds, center: f64, factor: f64, x: &[f64], y: &[f64]) -> PlotBounds {
    let (lo, hi) = (b.min()[0], b.max()[0]);
    fit_y(center - (center - lo) * factor, center + (hi - center) * factor, x, y)
}

/// Scale one axis by factor (< 1 zooms in) around anchor.
fn scale_axis(b: &PlotBounds, is_x: bool, anchor: f64, factor: f64) -> PlotBounds {
    let d = if is_x { 0 } else { 1 };
    let (mut min, mut max) = (b.min(), b.max());
    min[d] = anchor - (anchor - min[d]) * factor;
    max[d] = anchor + (max[d] - anchor) * factor;
    PlotBounds::from_min_max(min, max)
}
