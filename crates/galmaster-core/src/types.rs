use image::RgbaImage;
use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant};
use uuid::Uuid;

/// Normalized rectangle in 0..1 relative to the capture target (window client area).
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct NormRect {
    pub x: f32,
    pub y: f32,
    pub w: f32,
    pub h: f32,
}

impl Default for NormRect {
    fn default() -> Self {
        // Bottom subtitle band
        Self {
            x: 0.05,
            y: 0.78,
            w: 0.90,
            h: 0.18,
        }
    }
}

impl NormRect {
    pub fn clamp(self) -> Self {
        let x = self.x.clamp(0.0, 1.0);
        let y = self.y.clamp(0.0, 1.0);
        let w = self.w.clamp(0.0, 1.0 - x);
        let h = self.h.clamp(0.0, 1.0 - y);
        Self { x, y, w, h }
    }

    /// Convert to pixel rect given full image dimensions.
    pub fn to_pixel(&self, width: u32, height: u32) -> PixelRect {
        let x = (self.x * width as f32).round() as u32;
        let y = (self.y * height as f32).round() as u32;
        let w = (self.w * width as f32).round() as u32;
        let h = (self.h * height as f32).round() as u32;
        PixelRect {
            x: x.min(width.saturating_sub(1)),
            y: y.min(height.saturating_sub(1)),
            w: w.max(1).min(width.saturating_sub(x)),
            h: h.max(1).min(height.saturating_sub(y)),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct PixelRect {
    pub x: u32,
    pub y: u32,
    pub w: u32,
    pub h: u32,
}

/// Identifier for a captured window (platform-specific id as string).
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct WindowId(pub String);

#[derive(Debug, Clone)]
pub struct Frame {
    pub image: RgbaImage,
    pub captured_at: Instant,
    pub source_window: Option<WindowId>,
    pub roi: NormRect,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceKind {
    ClassicOcr,
    VisionExtract,
    Asr,
    E2e,
    Manual,
}

#[derive(Debug, Clone)]
pub struct TextSegment {
    pub id: Uuid,
    pub text: String,
    pub lang_hint: Option<String>,
    pub confidence: f32,
    pub bbox: Option<PixelRect>,
    pub t_start: Instant,
    pub source: SourceKind,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UnderstandingResult {
    pub original: Option<String>,
    pub translated: String,
    pub confidence: f32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub raw_model: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PipelineProfileKind {
    ExtractThenTranslate,
    VisionE2e,
    /// Reserved for future ASR path.
    AsrThenTranslate,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct LatencyBreakdown {
    pub capture_ms: u64,
    pub extract_ms: u64,
    pub translate_ms: u64,
    pub total_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TranslationEvent {
    pub id: Uuid,
    pub original: Option<String>,
    pub translated: String,
    pub profile: PipelineProfileKind,
    pub latency: LatencyBreakdown,
    pub ts_unix_ms: u64,
}

impl TranslationEvent {
    pub fn now(
        original: Option<String>,
        translated: String,
        profile: PipelineProfileKind,
        latency: LatencyBreakdown,
    ) -> Self {
        Self {
            id: Uuid::new_v4(),
            original,
            translated,
            profile,
            latency,
            ts_unix_ms: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_millis() as u64)
                .unwrap_or(0),
        }
    }
}

#[derive(Debug, Clone)]
pub struct TranslateRequest {
    pub text: String,
    pub target_lang: String,
    pub source_lang: Option<String>,
    pub previous_lines: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct TranslateResponse {
    pub translated: String,
    pub detected_lang: Option<String>,
}

#[derive(Debug, Clone)]
pub struct UnderstandContext {
    pub target_lang: String,
    pub previous_lines: Vec<String>,
}

/// Shared pipeline control messages (UI → worker).
#[derive(Debug, Clone)]
pub enum ControlMessage {
    UpdateConfig(Box<crate::config::Config>),
    Start,
    Stop,
    InjectMockEvent(TranslationEvent),
}

/// How long ago a frame was captured (for drop decisions).
pub fn frame_age(frame: &Frame) -> Duration {
    frame.captured_at.elapsed()
}
