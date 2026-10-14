//! The native macOS menu bar (the one at the top of the screen).
//!
//! egui draws its own in-window menus, so a real menu bar needs the `muda`
//! crate. Menu clicks and their shortcuts arrive as events on a channel, which
//! the app polls every frame.
//!
//!     ShapMass      About / Hide / Quit (⌘Q)
//!     File          Open… (⌘O), Add New Window (⌘N)

use std::sync::mpsc::{self, Receiver};

use eframe::egui;
use muda::accelerator::{Accelerator, Code, Modifiers};
use muda::{Menu, MenuEvent, MenuId, MenuItem, PredefinedMenuItem, Submenu};

/// What the user picked in the menu bar.
pub enum MenuAction {
    Open,
    NewWindow,
}

pub struct NativeMenu {
    _menu: Menu, // must stay alive for as long as the menu is shown
    open_id: MenuId,
    new_window_id: MenuId,
    events: Receiver<MenuEvent>,
}

impl NativeMenu {
    /// Install the menu bar. Call once, from the main thread, after the window exists.
    pub fn install(ctx: &egui::Context) -> Option<Self> {
        let menu = Menu::new();

        let app_menu = Submenu::with_items(
            "ShapMass",
            true,
            &[
                &PredefinedMenuItem::about(None, None),
                &PredefinedMenuItem::separator(),
                &PredefinedMenuItem::hide(None),
                &PredefinedMenuItem::separator(),
                &PredefinedMenuItem::quit(None),
            ],
        )
        .ok()?;

        let command = Modifiers::META; // the ⌘ key
        let open = MenuItem::new("Open…", true, Some(Accelerator::new(command, Code::KeyO)));
        let new_window = MenuItem::new("Add New Window", true, Some(Accelerator::new(command, Code::KeyN)));
        let file_menu = Submenu::with_items("File", true, &[&open, &new_window]).ok()?;

        menu.append_items(&[&app_menu, &file_menu]).ok()?;
        menu.init_for_nsapp();

        // Forward menu events to us, and wake the UI so it notices them.
        let (tx, events) = mpsc::channel();
        let ctx = ctx.clone();
        MenuEvent::set_event_handler(Some(move |event| {
            let _ = tx.send(event);
            ctx.request_repaint();
        }));

        Some(Self { open_id: open.id().clone(), new_window_id: new_window.id().clone(), _menu: menu, events })
    }

    /// Menu items picked since the last call.
    pub fn poll(&self) -> Vec<MenuAction> {
        let mut actions = Vec::new();
        while let Ok(event) = self.events.try_recv() {
            if event.id == self.open_id {
                actions.push(MenuAction::Open);
            } else if event.id == self.new_window_id {
                actions.push(MenuAction::NewWindow);
            }
        }
        actions
    }
}
