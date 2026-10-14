//! The main window. Rust twin of `python/main_window.py` + `spectrum_dock.py`.
//!
//! Layout:
//!     - Top:    the TIC.
//!     - Below:  spectrum windows (egui_dock tabs). Drag a tab to rearrange,
//!               split, stack, or pull it out into a floating window.
//!
//! Interaction (see plots.rs for zoom/pan controls):
//!     - Click the TIC          -> show that scan in the active spectrum window.
//!     - Click-drag on the TIC  -> average that RT range into the active window.
//!     - Hold Shift for either  -> put the result in a new window instead.
//!     - File > Add New Window  -> empty window that becomes the active one.
//!     - Left/right arrows over a plot -> step scans (Shift: grow/shrink an average).
//!
//! The active window's tab is highlighted; click inside a spectrum to make it active.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, Receiver};

use eframe::egui::{self, Color32, Key, KeyboardShortcut, Modifiers, RichText, WidgetText};
use egui_dock::tab_viewer::OnCloseResponse;
use egui_dock::{DockArea, DockState, Node, NodeIndex, Style, TabViewer, Tree};

#[cfg(target_os = "macos")]
use crate::native_menu::{MenuAction, NativeMenu};
use crate::plots::{self, Kind, Marker, PlotState};
use crate::raw_reader::RawFile;

const ACTIVE_TAB: Color32 = Color32::from_rgb(190, 210, 255);

/// One spectrum window.
struct SpectrumWindow {
    title: String,
    scan_type: String,
    mz: Vec<f64>,
    intensity: Vec<f64>,
    /// Scan numbers shown as (first, last); first == last for a single scan.
    scans: Option<(usize, usize)>,
    /// Matching retention-time range, for marking the TIC.
    rt_range: Option<(f64, f64)>,
    plot: PlotState,
}

/// Things the spectrum windows ask for while being drawn (handled afterwards,
/// because the windows are borrowed while drawing).
enum Action {
    Activate(usize),
    Step(usize, i64, bool),
    Closed(usize),
}

pub struct ShapMassApp {
    raw: Option<RawFile>,
    file_name: String,
    status: String,
    tic: (Vec<f64>, Vec<f64>),
    tic_plot: PlotState,
    windows: HashMap<usize, SpectrumWindow>,
    dock: DockState<usize>, // tabs are window ids
    order: Vec<usize>,      // open window ids, oldest first
    active: Option<usize>,
    next_id: usize,
    /// Result of the Open dialog, which runs on its own thread (see `start_open_dialog`).
    open_dialog: Option<Receiver<Option<PathBuf>>>,
    /// The real macOS menu bar. If it can't be installed, the in-window menu is used.
    #[cfg(target_os = "macos")]
    native_menu: Option<NativeMenu>,
}

impl ShapMassApp {
    pub fn new(cc: &eframe::CreationContext<'_>, open: Option<PathBuf>) -> Self {
        cc.egui_ctx.set_visuals(egui::Visuals::light()); // white, like the Python app
        let mut app = Self::empty();
        #[cfg(target_os = "macos")]
        {
            app.native_menu = NativeMenu::install(&cc.egui_ctx);
        }
        if let Some(path) = open {
            app.open_file(&path);
        }
        app
    }

    fn empty() -> Self {
        Self {
            raw: None,
            file_name: String::new(),
            status: "File → Open… (or drop a .raw file on the window) to load a Thermo .raw file".into(),
            tic: (Vec::new(), Vec::new()),
            tic_plot: PlotState::default(),
            windows: HashMap::new(),
            dock: DockState::new(Vec::new()),
            order: Vec::new(),
            active: None,
            next_id: 1,
            open_dialog: None,
            #[cfg(target_os = "macos")]
            native_menu: None,
        }
    }

    fn open_file(&mut self, path: &Path) {
        let raw = match RawFile::open(path) {
            Ok(raw) => raw,
            Err(err) => {
                self.status = format!("Could not open {}: {err}", path.display());
                return;
            }
        };
        self.tic = raw.tic();
        self.file_name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        self.status = format!(
            "{}  |  scans {}–{}  |  RT {:.2}–{:.2} min",
            raw.instrument_model, raw.first_scan, raw.last_scan,
            self.tic.0.first().unwrap_or(&0.0), self.tic.0.last().unwrap_or(&0.0)
        );
        self.raw = Some(raw);

        // A new file starts with just the TIC; old spectra belong to the old file.
        self.windows.clear();
        self.order.clear();
        self.active = None;
        self.dock = DockState::new(Vec::new());
        self.tic_plot.reset_view();
    }

    // --- Spectra -----------------------------------------------------------

    fn show_scan_at(&mut self, rt: f64, new_window: bool) {
        let Some(raw) = &self.raw else { return };
        let scan = raw.scan_number_at(rt);
        let id = self.target_window(new_window);
        self.show_scans(id, scan, scan, false);
    }

    fn average_range(&mut self, rt_start: f64, rt_end: f64, new_window: bool) {
        let Some(raw) = &self.raw else { return };
        let first = raw.scan_number_at(rt_start);
        // End on a scan of the same type as the first, so both ends are comparable.
        let last = raw.snap_to_type(raw.scan_number_at(rt_end), first);
        let id = self.target_window(new_window);
        self.show_scans(id, first, last, false);
    }

    /// Single scan: previous/next scan of the same type. Average: slide by one
    /// scan. Shift on an average: move only the right edge (grow/shrink).
    fn step_scans(&mut self, id: usize, step: i64, extend: bool) {
        let Some(raw) = &self.raw else { return };
        let Some((first, last)) = self.windows.get(&id).and_then(|w| w.scans) else { return };
        let (new_first, new_last) = if extend {
            let new_last = raw.step_scan(last, step).max(first);
            if new_last == last { return; }
            (first, new_last)
        } else {
            let (nf, nl) = (raw.step_scan(first, step), raw.step_scan(last, step));
            if nf == first || nl == last { return; } // hit the start or end of the file
            (nf, nl)
        };
        self.active = Some(id);
        self.show_scans(id, new_first, new_last, true);
    }

    /// Draw scan `first` (if first == last) or the average of first..=last.
    fn show_scans(&mut self, id: usize, first: usize, last: usize, keep_view: bool) {
        let Some(raw) = &self.raw else { return };
        let (mz, intensity) = if first == last { raw.spectrum(first) } else { raw.average(first, last) };
        let (start, end) = (raw.retention_time(first), raw.retention_time(last));
        let title = if first == last {
            format!("Scan {first}  ({start:.2} min)")
        } else {
            format!("Average scans {first}–{last}  ({start:.2}–{end:.2} min)")
        };
        let scan_type = raw.scan_type(first).to_string();
        if let Some(w) = self.windows.get_mut(&id) {
            w.title = title;
            w.scan_type = scan_type;
            w.mz = mz;
            w.intensity = intensity;
            w.scans = Some((first, last));
            w.rt_range = Some((start, end));
            if keep_view {
                w.plot.refit_y(&w.mz, &w.intensity);
            } else {
                w.plot.reset_view();
            }
        }
    }

    /// The window a TIC click/drag should draw into.
    fn target_window(&mut self, new_window: bool) -> usize {
        match self.active {
            Some(id) if !new_window => id,
            _ => self.new_window(),
        }
    }

    /// Add an empty spectrum window to the right and make it active.
    fn new_window(&mut self) -> usize {
        let id = self.next_id;
        self.next_id += 1;
        self.windows.insert(id, SpectrumWindow {
            title: format!("Spectrum {id}"),
            scan_type: String::new(),
            mz: Vec::new(),
            intensity: Vec::new(),
            scans: None,
            rt_range: None,
            plot: PlotState::default(),
        });

        // Split to the right of the newest window still docked in the main area.
        let tree = self.dock.main_surface_mut();
        let newest_docked = self.order.iter().rev().find_map(|t| tree.find_tab(t).map(|(node, _)| node));
        match newest_docked {
            Some(node) => { tree.split_right(node, 0.5, vec![id]); }
            None => *tree = Tree::new(vec![id]),
        }
        self.order.push(id);
        self.active = Some(id);
        even_out_columns(self.dock.main_surface_mut());
        id
    }

    fn window_closed(&mut self, id: usize) {
        self.windows.remove(&id);
        self.order.retain(|&t| t != id);
        if self.active == Some(id) {
            self.active = self.order.last().copied(); // hand "active" to the newest remaining
        }
    }

    // --- Drawing -------------------------------------------------------------

    fn menu_bar(&mut self, ui: &mut egui::Ui) {
        let open = KeyboardShortcut::new(Modifiers::COMMAND, Key::O);
        let new_window = KeyboardShortcut::new(Modifiers::COMMAND, Key::N);
        let quit = KeyboardShortcut::new(Modifiers::COMMAND, Key::Q);
        let (do_open, do_new, do_quit) = ui.input_mut(|i| {
            (i.consume_shortcut(&open), i.consume_shortcut(&new_window), i.consume_shortcut(&quit))
        });
        let (mut do_open, mut do_new, mut do_quit) = (do_open, do_new, do_quit);

        egui::MenuBar::new().ui(ui, |ui| {
            ui.menu_button("File", |ui| {
                do_open |= ui.add(egui::Button::new("Open…").shortcut_text(ui.ctx().format_shortcut(&open))).clicked();
                do_new |= ui.add(egui::Button::new("Add New Window").shortcut_text(ui.ctx().format_shortcut(&new_window))).clicked();
                ui.separator();
                do_quit |= ui.add(egui::Button::new("Quit").shortcut_text(ui.ctx().format_shortcut(&quit))).clicked();
            });
        });

        if do_open {
            self.start_open_dialog(ui.ctx());
        }
        if do_new {
            self.new_window();
        }
        if do_quit {
            ui.ctx().send_viewport_cmd(egui::ViewportCommand::Close);
        }
    }

    fn has_native_menu(&self) -> bool {
        #[cfg(target_os = "macos")]
        {
            self.native_menu.is_some()
        }
        #[cfg(not(target_os = "macos"))]
        {
            false
        }
    }

    /// Handle clicks and shortcuts from the macOS menu bar.
    fn poll_native_menu(&mut self, ctx: &egui::Context) {
        #[cfg(target_os = "macos")]
        {
            let actions = self.native_menu.as_ref().map(|m| m.poll()).unwrap_or_default();
            for action in actions {
                match action {
                    MenuAction::Open => self.start_open_dialog(ctx),
                    MenuAction::NewWindow => {
                        self.new_window();
                    }
                }
            }
        }
        #[cfg(not(target_os = "macos"))]
        let _ = ctx;
    }

    /// Show the macOS file picker without blocking the draw loop.
    ///
    /// A blocking dialog opened from inside a frame re-enters the window
    /// system's event handler, which is unreliable on macOS. Instead the dialog
    /// runs from a helper thread (rfd hops to the main thread itself) and the
    /// chosen path is picked up on a later frame by `poll_open_dialog`.
    fn start_open_dialog(&mut self, ctx: &egui::Context) {
        if self.open_dialog.is_some() {
            return; // already open
        }
        let (tx, rx) = mpsc::channel();
        let ctx = ctx.clone();
        std::thread::spawn(move || {
            let picked = rfd::FileDialog::new()
                .add_filter("Thermo RAW files", &["raw"])
                .set_title("Open Thermo RAW file")
                .pick_file();
            let _ = tx.send(picked);
            ctx.request_repaint(); // wake the UI so it sees the result
        });
        self.open_dialog = Some(rx);
        self.status = "Choose a .raw file…".into();
    }

    /// Open a file picked in the dialog, or dropped onto the window.
    fn poll_open_dialog(&mut self, ctx: &egui::Context) {
        if let Some(rx) = &self.open_dialog
            && let Ok(picked) = rx.try_recv()
        {
            self.open_dialog = None;
            match picked {
                Some(path) => self.open_file(&path),
                None => self.status = "Open cancelled".into(),
            }
        }
        // Drag-and-drop: drop a .raw file anywhere on the window.
        let dropped = ctx.input(|i| i.raw.dropped_files.iter().find_map(|f| Some(f.path().to_path_buf())));
        if let Some(path) = dropped {
            self.open_file(&path);
        }
    }

    fn tic_panel(&mut self, ui: &mut egui::Ui) {
        ui.vertical_centered(|ui| ui.label(RichText::new("TIC").strong()));
        let marker = match self.active.and_then(|id| self.windows.get(&id)).and_then(|w| w.rt_range) {
            Some((a, b)) if a == b => Marker::Line(a),
            Some((a, b)) => Marker::Band(a, b),
            None => Marker::None,
        };
        let ev = plots::show(
            ui, egui::Id::new("tic"), &mut self.tic_plot, Kind::Chromatogram,
            &self.tic.0, &self.tic.1, ("Retention time (min)", "Intensity"), marker,
        );
        if let Some(rt) = ev.clicked {
            self.show_scan_at(rt, ev.shift);
        }
        if let Some((a, b)) = ev.drag_finished {
            self.average_range(a, b, ev.shift);
        }
        if let (Some(step), Some(id)) = (ev.step, self.active) {
            self.step_scans(id, step, ev.shift);
        }
    }
}

impl eframe::App for ShapMassApp {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        let title = if self.file_name.is_empty() { "ShapMass (Rust)".into() } else { format!("ShapMass (Rust) — {}", self.file_name) };
        ui.ctx().send_viewport_cmd(egui::ViewportCommand::Title(title));

        self.poll_native_menu(&ui.ctx().clone());
        self.poll_open_dialog(&ui.ctx().clone());

        if !self.has_native_menu() {
            egui::Panel::top("menu").show(ui, |ui| self.menu_bar(ui));
        }
        egui::Panel::bottom("status").show(ui, |ui| ui.label(&self.status));

        if self.windows.is_empty() {
            // Only the TIC until the first spectrum is made.
            egui::CentralPanel::default().show(ui, |ui| self.tic_panel(ui));
            return;
        }

        egui::Panel::top("tic")
            .resizable(true)
            .default_size(300.0)
            .show(ui, |ui| self.tic_panel(ui));

        let mut viewer = Viewer { windows: &mut self.windows, active: self.active, actions: Vec::new() };
        egui::CentralPanel::default().show(ui, |ui| {
            DockArea::new(&mut self.dock)
                .style(Style::from_egui(ui.style().as_ref()))
                .show_inside(ui, &mut viewer);
        });

        let actions = std::mem::take(&mut viewer.actions);
        let mut closed_any = false;
        for action in actions {
            match action {
                Action::Activate(id) => self.active = Some(id),
                Action::Step(id, step, shift) => self.step_scans(id, step, shift),
                Action::Closed(id) => { self.window_closed(id); closed_any = true; }
            }
        }
        if closed_any {
            even_out_columns(self.dock.main_surface_mut());
        }
    }
}

/// Draws each spectrum tab for egui_dock.
struct Viewer<'a> {
    windows: &'a mut HashMap<usize, SpectrumWindow>,
    active: Option<usize>,
    actions: Vec<Action>,
}

impl TabViewer for Viewer<'_> {
    type Tab = usize;

    fn id(&mut self, tab: &mut usize) -> egui::Id {
        egui::Id::new(("spectrum_tab", *tab))
    }

    fn title(&mut self, tab: &mut usize) -> WidgetText {
        let title = self.windows.get(tab).map(|w| w.title.as_str()).unwrap_or("?");
        let text = RichText::new(title);
        if self.active == Some(*tab) { text.background_color(ACTIVE_TAB).into() } else { text.into() }
    }

    fn ui(&mut self, ui: &mut egui::Ui, tab: &mut usize) {
        let Some(w) = self.windows.get_mut(tab) else { return };
        ui.vertical_centered(|ui| ui.label(RichText::new(&w.scan_type).size(11.0)));
        let ev = plots::show(
            ui, egui::Id::new(("spectrum", *tab)), &mut w.plot, Kind::Spectrum,
            &w.mz, &w.intensity, ("m/z", "Intensity"), Marker::None,
        );
        if ev.pressed {
            self.actions.push(Action::Activate(*tab));
        }
        if let Some(step) = ev.step {
            self.actions.push(Action::Activate(*tab));
            self.actions.push(Action::Step(*tab, step, ev.shift));
        }
    }

    fn on_close(&mut self, tab: &mut usize) -> OnCloseResponse {
        self.actions.push(Action::Closed(*tab));
        OnCloseResponse::Close
    }
}

/// Give side-by-side windows equal widths: each left/right split gets the
/// fraction of columns on its left side.
fn even_out_columns(tree: &mut Tree<usize>) {
    fn columns(tree: &Tree<usize>, i: NodeIndex) -> usize {
        if i.0 >= tree.len() {
            return 0;
        }
        match &tree[i] {
            Node::Leaf(leaf) if !leaf.tabs.is_empty() => 1,
            Node::Horizontal(_) => columns(tree, i.left()) + columns(tree, i.right()),
            Node::Vertical(_) => columns(tree, i.left()).max(columns(tree, i.right())),
            _ => 0,
        }
    }
    for i in 0..tree.len() {
        let i = NodeIndex(i);
        let (left, right) = (columns(tree, i.left()), columns(tree, i.right()));
        if let Node::Horizontal(split) = &mut tree[i]
            && left + right > 0
        {
            split.fraction = left as f32 / (left + right) as f32;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn titles(app: &ShapMassApp) -> Vec<String> {
        app.order.iter().map(|id| app.windows[id].title.clone()).collect()
    }
    fn active(app: &ShapMassApp) -> String {
        app.active.map(|id| app.windows[&id].title.clone()).unwrap_or_default()
    }
    fn fractions(app: &mut ShapMassApp) -> Vec<f32> {
        let tree = app.dock.main_surface_mut();
        (0..tree.len()).filter_map(|i| match &tree[NodeIndex(i)] { Node::Horizontal(s) => Some(s.fraction), _ => None }).collect()
    }

    #[test]
    fn window_routing_matches_python() {
        let mut app = ShapMassApp::empty();
        app.open_file(Path::new("../../Raw Files/NISTmAB_QE_C_3_phase_200ng_on_column_1.raw"));
        assert!(app.windows.is_empty(), "only the TIC after opening");

        app.show_scan_at(7.5, false);
        println!("click              {:?}  active={}", titles(&app), active(&app));
        app.show_scan_at(8.0, false);
        println!("click again        {:?}", titles(&app));
        assert_eq!(app.windows.len(), 1);
        app.average_range(7.5, 8.5, false);
        println!("drag               {:?}", titles(&app));
        app.show_scan_at(9.0, true);
        app.average_range(10.0, 11.0, true);
        println!("shift click + drag {:?}  active={}", titles(&app), active(&app));
        assert_eq!(app.windows.len(), 3);
        println!("column fractions   {:?}", fractions(&mut app));

        let first = app.order[0];
        app.active = Some(first);
        app.show_scan_at(12.0, false);
        println!("activate 1 + click {:?}", titles(&app));

        app.step_scans(first, 1, false);
        println!("step right         {}", active(&app));
        let avg = app.order[2];
        app.step_scans(avg, 1, false);
        println!("slide average      {}", app.windows[&avg].title);
        app.step_scans(avg, 1, true);
        println!("shift: grow        {}", app.windows[&avg].title);

        app.new_window();
        println!("add new window     {:?}  active={}", titles(&app), active(&app));
        println!("column fractions   {:?}", fractions(&mut app));

        let id = app.order[1];
        let found = app.dock.main_surface().find_tab(&id).unwrap();
        app.dock.main_surface_mut().remove_tab(found);
        app.window_closed(id);
        even_out_columns(app.dock.main_surface_mut());
        println!("close one          {:?}  active={}", titles(&app), active(&app));
        println!("column fractions   {:?}", fractions(&mut app));
    }
}
