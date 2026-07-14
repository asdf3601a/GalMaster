use crate::Extractor;
use async_trait::async_trait;
use galmaster_capture::frame_to_png_bytes;
use galmaster_core::types::{Frame, SourceKind, TextSegment};
use galmaster_provider::{
    AnthropicClient, ChatMessage, ChatRequestOptions, LlmSamplingParams, MessageContent,
    OpenAiClient, ProviderConfig,
};
use tokio_util::sync::CancellationToken;
use tracing::debug;
use uuid::Uuid;

const DEFAULT_SYSTEM: &str = r#"You are a subtitle OCR engine. Read ONLY the subtitle / caption text visible in the image.
Rules:
- Output plain text of the subtitle lines only.
- Do not translate.
- Do not describe the image or UI.
- If no subtitle text is visible, output an empty string.
- Preserve line breaks between subtitle lines."#;

pub enum VisionProviderKind {
    OpenAi(OpenAiClient),
    Anthropic(AnthropicClient),
}

pub struct VisionModelExtractor {
    provider: VisionProviderKind,
    system_prompt: String,
    sampling: LlmSamplingParams,
}

impl VisionModelExtractor {
    pub fn openai(cfg: ProviderConfig, sampling: LlmSamplingParams) -> anyhow::Result<Self> {
        Ok(Self {
            provider: VisionProviderKind::OpenAi(OpenAiClient::new(cfg)?),
            system_prompt: DEFAULT_SYSTEM.into(),
            sampling,
        })
    }

    pub fn anthropic(cfg: ProviderConfig, sampling: LlmSamplingParams) -> anyhow::Result<Self> {
        Ok(Self {
            provider: VisionProviderKind::Anthropic(AnthropicClient::new(cfg)?),
            system_prompt: DEFAULT_SYSTEM.into(),
            sampling,
        })
    }

    pub fn with_system_prompt(mut self, prompt: impl Into<String>) -> Self {
        self.system_prompt = prompt.into();
        self
    }
}

#[async_trait]
impl Extractor for VisionModelExtractor {
    async fn extract(
        &mut self,
        frame: &Frame,
        cancel: &CancellationToken,
    ) -> anyhow::Result<Vec<TextSegment>> {
        let png = frame_to_png_bytes(frame)?;
        let messages = [
            ChatMessage {
                role: "system".into(),
                content: MessageContent::Text(self.system_prompt.clone()),
            },
            ChatMessage {
                role: "user".into(),
                content: MessageContent::Parts {
                    text: "Extract the subtitle text from this image.".into(),
                    image_png: Some(png),
                },
            },
        ];

        let opts = ChatRequestOptions::default();
        let result = match &self.provider {
            VisionProviderKind::OpenAi(c) => {
                c.chat(&messages, &self.sampling, &opts, cancel).await?
            }
            VisionProviderKind::Anthropic(c) => {
                c.chat(&messages, &self.sampling, &opts, cancel).await?
            }
        };

        let text = result.text.trim().to_string();
        debug!(len = text.len(), "vision extract done");

        if text.is_empty() {
            return Ok(vec![]);
        }

        Ok(vec![TextSegment {
            id: Uuid::new_v4(),
            text,
            lang_hint: None,
            confidence: 0.9,
            bbox: None,
            t_start: frame.captured_at,
            source: SourceKind::VisionExtract,
        }])
    }
}
