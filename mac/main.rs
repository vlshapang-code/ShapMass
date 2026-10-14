//! Launch ShapMass (Rust). Rust twin of `main.py`.
//!
//! The rest of the Rust code and Cargo.toml are in rust/. Run from the mac/rust/ folder:
//!     cargo run --release
//!     cargo run --release -- "../../Raw Files/some_file.raw"     (open a file right away)

#[path = "rust/app.rs"]
mod app;
#[cfg(target_os = "macos")]
#[path = "rust/native_menu.rs"]
mod native_menu;
#[path = "rust/plots.rs"]
mod plots;
#[path = "rust/raw_reader.rs"]
mod raw_reader;

use std::path::PathBuf;

use eframe::egui;

fn main() -> eframe::Result {
    // Thermo's reader runs on .NET 8, which we installed in ~/.dotnet.
    if std::env::var_os("DOTNET_ROOT").is_none()
        && let Some(home) = std::env::var_os("HOME")
    {
        // SAFETY: nothing else is running yet, so no other thread reads the environment.
        unsafe { std::env::set_var("DOTNET_ROOT", PathBuf::from(home).join(".dotnet")) };
    }

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
