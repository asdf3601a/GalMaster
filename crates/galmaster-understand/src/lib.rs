//! Vision end-to-end: image → original + translated subtitle.

use async_trait::async_trait;
use galmaster_capture::frame_to_png_bytes;
use galmaster_core::types::{Frame, UnderstandContext, UnderstandingResult};
use galmaster_provider::{
    strip_code_fence, AnthropicClient, ChatMessage, MessageContent, OpenAiClient, ProviderConfig,
};
use serde::Deserialize;
use tokio_util::sync::CancellationToken;
use tracing::debug;

#[async_trait]
pub trait VisionUnderstanding: Send {
    async fn understand(
        &mut self,
        frame: &Frame,
        ctx: &UnderstandContext,
        cancel: &CancellationToken,
    ) -> anyhow::Result<UnderstandingResult>;
}

enum ProviderKind {
    OpenAi(OpenAiClient),
    Anthropic(AnthropicClient),
}

pub struct VisionE2e {
    provider: ProviderKind,
}

impl VisionE2e {
    pub fn openai(cfg: ProviderConfig) -> anyhow::Result<Self> {
        Ok(Self {
            provider: ProviderKind::OpenAi(OpenAiClient::new(cfg)?),
        })
    }

    pub fn anthropic(cfg: ProviderConfig) -> anyhow::Result<Self> {
        Ok(Self {
            provider: ProviderKind::Anthropic(AnthropicClient::new(cfg)?),
        })
    }
}

#[derive(Debug, Deserialize)]
struct E2eJson {
    #[serde(default)]
    original: Option<String>,
    #[serde(default)]
    translated: Option<String>,
    /// Some models use "translation" key
    #[serde(default)]
    translation: Option<String>,
}

fn system_prompt(target_lang: &str) -> String {
    format!(
        r#"You are a real-time subtitle translator.
Look at the image and read the on-screen subtitle / caption text only (ignore UI chrome).
Respond with a single JSON object only:
{{"original":"<source text or empty>","translated":"<translation into {target_lang}>"}}
Rules:
- If no subtitle is visible, return {{"original":"","translated":""}}.
- Do not add explanations or markdown."#
    )
}

#[async_trait]
impl VisionUnderstanding for VisionE2e {
    async fn understand(
        &mut self,
        frame: &Frame,
        ctx: &UnderstandContext,
        cancel: &CancellationToken,
    ) -> anyhow::Result<UnderstandingResult> {
        let png = frame_to_png_bytes(frame)?;
        let mut user = format!(
            "Target language: {}.\nExtract and translate the subtitle.",
            ctx.target_lang
        );
        if !ctx.previous_lines.is_empty() {
            user.push_str("\nPrevious lines for context:\n");
            for line in &ctx.previous_lines {
                user.push_str("- ");
                user.push_str(line);
                user.push('\n');
            }
        }

        let messages = [
            ChatMessage {
                role: "system".into(),
                content: MessageContent::Text(system_prompt(&ctx.target_lang)),
            },
            ChatMessage {
                role: "user".into(),
                content: MessageContent::Parts {
                    text: user,
                    image_png: Some(png),
                },
            },
        ];

        let result = match &self.provider {
            ProviderKind::OpenAi(c) => c.chat(&messages, 0.1, cancel).await?,
            ProviderKind::Anthropic(c) => c.chat(&messages, 0.1, cancel).await?,
        };

        let cleaned = strip_code_fence(&result.text);
        debug!(raw = %cleaned, "vision e2e raw");

        let parsed = parse_e2e(&cleaned);
        Ok(UnderstandingResult {
            original: parsed.0,
            translated: parsed.1,
            confidence: 0.85,
            raw_model: Some(result.text),
        })
    }
}

fn parse_e2e(text: &str) -> (Option<String>, String) {
    if let Ok(j) = serde_json::from_str::<E2eJson>(text) {
        let translated = j
            .translated
            .or(j.translation)
            .unwrap_or_default()
            .trim()
            .to_string();
        let original = j
            .original
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty());
        return (original, translated);
    }
    // Fallback: treat whole response as translation
    (None, text.trim().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_json() {
        let (o, t) = parse_e2e(r#"{"original":"Hello","translated":"你好"}"#);
        assert_eq!(o.as_deref(), Some("Hello"));
        assert_eq!(t, "你好");
    }
}
