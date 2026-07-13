//! GalMaster core: types, gates, config, and pipeline orchestration.

pub mod config;
pub mod gate;
pub mod pipeline;
pub mod style;
pub mod types;

pub use config::Config;
pub use gate::{FrameGate, ResultGate, TextGate};
pub use pipeline::{Pipeline, PipelineHandle};
pub use style::SubtitleStyle;
pub use types::*;
