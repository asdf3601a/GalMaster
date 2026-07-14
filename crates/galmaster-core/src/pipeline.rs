//! Pipeline orchestration: wires gates + backends via channels.
//!
//! Concrete Extractor / Translator / VisionUnderstanding implementations live
//! in other crates; this module owns the control loop shape and latest-wins
//! queue semantics.

use crate::config::Config;
use crate::gate::{FrameGate, ResultGate, TextGate};
use crate::types::{
    ControlMessage, Frame, LatencyBreakdown, PipelineProfileKind, TranslationEvent,
};
use std::sync::{Arc, Mutex as StdMutex};
use tokio::sync::{broadcast, mpsc, watch, Mutex};
use tokio_util::sync::CancellationToken;
use tracing::{debug, info, warn};

/// Capacity-1 latest-wins frame slot.
#[derive(Clone, Default)]
pub struct LatestFrame {
    inner: Arc<Mutex<Option<Frame>>>,
}

impl LatestFrame {
    pub fn new() -> Self {
        Self::default()
    }

    pub async fn push(&self, frame: Frame) {
        let mut g = self.inner.lock().await;
        *g = Some(frame);
    }

    pub async fn take(&self) -> Option<Frame> {
        let mut g = self.inner.lock().await;
        g.take()
    }
}

/// Fan-out of translation events to UI / OBS.
pub type EventTx = broadcast::Sender<TranslationEvent>;
pub type EventRx = broadcast::Receiver<TranslationEvent>;
pub type StyleTx = watch::Sender<crate::style::SubtitleStyle>;
pub type StyleRx = watch::Receiver<crate::style::SubtitleStyle>;

/// Shared human-readable pipeline status (UI + worker).
pub type StatusSlot = Arc<StdMutex<String>>;

#[derive(Clone)]
pub struct PipelineHandle {
    pub events: EventTx,
    pub control: mpsc::UnboundedSender<ControlMessage>,
    pub frames: LatestFrame,
    pub style: StyleTx,
    pub running: watch::Sender<bool>,
    /// Short status line for the GUI (e.g. "Recognizing…", "Done — 320 ms").
    pub status: StatusSlot,
}

impl PipelineHandle {
    pub fn subscribe_events(&self) -> EventRx {
        self.events.subscribe()
    }

    pub fn style_rx(&self) -> StyleRx {
        self.style.subscribe()
    }

    pub fn set_running(&self, on: bool) {
        let _ = self.running.send(on);
        let msg = if on {
            ControlMessage::Start
        } else {
            ControlMessage::Stop
        };
        let _ = self.control.send(msg);
        if on {
            self.set_status("Running — waiting for capture…");
        } else {
            self.set_status("Stopped");
        }
    }

    pub fn set_status(&self, msg: impl Into<String>) {
        if let Ok(mut g) = self.status.lock() {
            *g = msg.into();
        }
    }

    pub fn status_text(&self) -> String {
        self.status
            .lock()
            .map(|g| g.clone())
            .unwrap_or_else(|_| "…".into())
    }

    pub fn publish_event(&self, event: TranslationEvent) {
        let _ = self.events.send(event);
    }

    pub fn update_style(&self, style: crate::style::SubtitleStyle) {
        let _ = self.style.send(style);
    }
}

pub struct Pipeline {
    pub config: Arc<Mutex<Config>>,
    pub handle: PipelineHandle,
}

impl Pipeline {
    pub fn new(config: Config) -> Self {
        let (events, _) = broadcast::channel(64);
        let (control, _control_rx) = mpsc::unbounded_channel();
        let (style, _) = watch::channel(config.style.clone());
        let (running, _) = watch::channel(false);
        let frames = LatestFrame::new();
        let status = Arc::new(StdMutex::new("Ready".into()));

        // Note: control_rx is re-created when spawn_worker is called with a full runner.
        // For handle-only construction we keep a dummy; binary rebuilds properly.
        let handle = PipelineHandle {
            events,
            control,
            frames,
            style,
            running,
            status,
        };

        Self {
            config: Arc::new(Mutex::new(config)),
            handle,
        }
    }

    /// Create pipeline + control channel receiver for the worker.
    pub fn create(config: Config) -> (Self, mpsc::UnboundedReceiver<ControlMessage>) {
        let (events, _) = broadcast::channel(64);
        let (control_tx, control_rx) = mpsc::unbounded_channel();
        let (style, _) = watch::channel(config.style.clone());
        let (running, _) = watch::channel(false);
        let frames = LatestFrame::new();
        let status = Arc::new(StdMutex::new("Ready".into()));

        let handle = PipelineHandle {
            events,
            control: control_tx,
            frames,
            style,
            running,
            status,
        };

        (
            Self {
                config: Arc::new(Mutex::new(config)),
                handle,
            },
            control_rx,
        )
    }
}

/// Shared gate state rebuilt from config.
pub struct GateBundle {
    pub frame: FrameGate,
    pub text: TextGate,
    pub result: ResultGate,
}

impl GateBundle {
    pub fn from_config(cfg: &Config) -> Self {
        Self {
            frame: FrameGate::new(cfg.gate.pixel_diff_threshold, cfg.gate.stable_frames),
            text: TextGate::new(cfg.gate.text_similarity_skip, cfg.gate.stable_frames),
            result: ResultGate::new(cfg.gate.text_similarity_skip),
        }
    }
}

/// Process one frame through extract→translate profile (sync helper for tests / mock).
pub fn mock_extract_then_translate_event(
    original: &str,
    translated: &str,
    latency: LatencyBreakdown,
) -> TranslationEvent {
    TranslationEvent::now(
        Some(original.to_string()),
        translated.to_string(),
        PipelineProfileKind::ExtractThenTranslate,
        latency,
    )
}

/// Apply control messages that only touch local gate/config state.
pub async fn apply_control(
    msg: ControlMessage,
    config: &Arc<Mutex<Config>>,
    gates: &mut GateBundle,
    running: &watch::Sender<bool>,
    style: &StyleTx,
    cancel: &CancellationToken,
) {
    match msg {
        ControlMessage::Start => {
            info!("pipeline start");
            let _ = running.send(true);
            gates.frame.reset();
            gates.text.reset();
            gates.result.reset();
        }
        ControlMessage::Stop => {
            info!("pipeline stop");
            let _ = running.send(false);
            cancel.cancel();
            gates.frame.reset();
            gates.text.reset();
            gates.result.reset();
        }
        ControlMessage::UpdateConfig(cfg) => {
            debug!("pipeline config update");
            *gates = GateBundle::from_config(&cfg);
            let _ = style.send(cfg.style.clone());
            *config.lock().await = *cfg;
        }
        ControlMessage::InjectMockEvent(ev) => {
            // Caller publishes; this is a no-op placeholder for completeness.
            let _ = ev;
        }
    }
}

pub fn warn_backend(name: &str, err: impl std::fmt::Display) {
    warn!(backend = name, error = %err, "backend error");
}
