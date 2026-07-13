//! Settings UI + subtitle overlay rendering helpers.

mod app;
mod style_editor;
mod system_fonts;

pub use app::{run_settings_app, AppShared, GalMasterApp};
pub use style_editor::{apply_custom_font, apply_fonts, style_editor_ui};
pub use system_fonts::css_font_stack;
