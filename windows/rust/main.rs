//! Launch ShapMass (Rust). Rust twin of `main.py`.
//!
//! The rest of the Rust code and Cargo.toml are in rust\. Run from the windows\rust\ folder:
//!     cargo run --release
//!     cargo run --release -- "..\..\Raw Files\some_file.raw"     (open a file right away)
//!
//! Thermo's reader runs on .NET 8, or a newer .NET if 8 isn't installed (see raw_reader.rs).
//! The usual system-wide install (C:\Program Files\dotnet) is found automatically;
//! set DOTNET_ROOT to use another.

// Release builds are GUI apps: don't open a console window alongside them.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod app;
mod plots;
mod raw_reader;

use std::path::PathBuf;

use eframe::egui;

fn main() -> eframe::Result {
    let open = std::env::args().nth(1).map(PathBuf::from);
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1200.0, 800.0])
            .with_title("ShapMass (Rust)"),
        ..Default::default()
    };
    eframe::run_native(
        "ShapMass (Rust)",
        options,
        Box::new(|cc| Ok(Box::new(app::ShapMassApp::new(cc, open)))),
    )
}
