//! Extractors: vision-model (default) and optional classic OCR stubs.

mod vision;

pub use vision::VisionModelExtractor;

use async_trait::async_trait;
use galmaster_core::types::{Frame, TextSegment};
use tokio_util::sync::CancellationToken;

#[async_trait]
pub trait Extractor: Send {
    async fn extract(
        &mut self,
        frame: &Frame,
        cancel: &CancellationToken,
    ) -> anyhow::Result<Vec<TextSegment>>;
}

/// Passthrough / mock extractor for tests and offline UI demos.
pub struct MockExtractor {
    pub text: String,
}

#[async_trait]
impl Extractor for MockExtractor {
    async fn extract(
        &mut self,
        _frame: &Frame,
        _cancel: &CancellationToken,
    ) -> anyhow::Result<Vec<TextSegment>> {
        Ok(vec![TextSegment {
            id: uuid::Uuid::new_v4(),
            text: self.text.clone(),
            lang_hint: None,
            confidence: 1.0,
            bbox: None,
            t_start: std::time::Instant::now(),
            source: galmaster_core::types::SourceKind::Manual,
        }])
    }
}

/// Classic OCR placeholder — enable real Tesseract behind `classic_ocr` later.
pub struct ClassicOcrExtractor;

#[async_trait]
impl Extractor for ClassicOcrExtractor {
    async fn extract(
        &mut self,
        _frame: &Frame,
        _cancel: &CancellationToken,
    ) -> anyhow::Result<Vec<TextSegment>> {
        anyhow::bail!(
            "classic_ocr backend is not linked in this build; use vision_model or enable the classic_ocr feature later"
        )
    }
}
