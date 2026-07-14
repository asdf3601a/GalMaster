//! Pure-text translators.

use async_trait::async_trait;
use galmaster_core::types::{TranslateRequest, TranslateResponse};
use galmaster_provider::{
    ChatClient, ChatMessage, ChatRequestOptions, LlmSamplingParams, MessageContent, ProviderConfig,
};
use tokio_util::sync::CancellationToken;
use tracing::debug;

#[async_trait]
pub trait Translator: Send + Sync {
    async fn translate(
        &self,
        req: TranslateRequest,
        cancel: &CancellationToken,
    ) -> anyhow::Result<TranslateResponse>;
}

pub struct TextTranslator {
    client: ChatClient,
    sampling: LlmSamplingParams,
}

impl TextTranslator {
    pub fn new(client: ChatClient, sampling: LlmSamplingParams) -> Self {
        Self { client, sampling }
    }

    pub fn openai(cfg: ProviderConfig, sampling: LlmSamplingParams) -> anyhow::Result<Self> {
        Ok(Self::new(ChatClient::openai(cfg)?, sampling))
    }

    pub fn anthropic(cfg: ProviderConfig, sampling: LlmSamplingParams) -> anyhow::Result<Self> {
        Ok(Self::new(ChatClient::anthropic(cfg)?, sampling))
    }

    /// Sugar for LiteRT-LM / local OpenAI-compatible servers.
    pub fn litert_lm_http(
        base_url: &str,
        model: &str,
        api_key: &str,
        sampling: LlmSamplingParams,
    ) -> anyhow::Result<Self> {
        let cfg = ProviderConfig::openai_compat(base_url, api_key, model);
        Self::openai(cfg, sampling)
    }
}

fn system_prompt(target_lang: &str) -> String {
    format!(
        r#"You are a real-time subtitle translator.
Translate the user's subtitle line(s) into {target_lang}.
Rules:
- Output ONLY the translation, no quotes, no notes.
- Keep proper nouns when appropriate.
- Match roughly the length of the source when possible.
- If the input is empty, output empty.
- If previous subtitles are given as context, use them only for disambiguation; translate only the current line."#
    )
}

#[async_trait]
impl Translator for TextTranslator {
    async fn translate(
        &self,
        req: TranslateRequest,
        cancel: &CancellationToken,
    ) -> anyhow::Result<TranslateResponse> {
        if req.text.trim().is_empty() {
            return Ok(TranslateResponse {
                translated: String::new(),
                detected_lang: None,
            });
        }

        let mut user = String::new();
        if !req.previous_lines.is_empty() {
            user.push_str("Context (previous subtitles; do not re-translate these):\n");
            for line in &req.previous_lines {
                user.push_str("- ");
                user.push_str(line);
                user.push('\n');
            }
            user.push_str("\nTranslate this line:\n");
        }
        user.push_str(&req.text);

        let messages = [
            ChatMessage {
                role: "system".into(),
                content: MessageContent::Text(system_prompt(&req.target_lang)),
            },
            ChatMessage {
                role: "user".into(),
                content: MessageContent::Text(user),
            },
        ];

        let opts = ChatRequestOptions::default();
        let result = self
            .client
            .chat(&messages, &self.sampling, &opts, cancel)
            .await?;

        let translated = result.text.trim().to_string();
        debug!(len = translated.len(), "translate done");
        Ok(TranslateResponse {
            translated,
            detected_lang: req.source_lang,
        })
    }
}

pub struct MockTranslator {
    pub map_prefix: String,
}

#[async_trait]
impl Translator for MockTranslator {
    async fn translate(
        &self,
        req: TranslateRequest,
        _cancel: &CancellationToken,
    ) -> anyhow::Result<TranslateResponse> {
        Ok(TranslateResponse {
            translated: format!("{}{}", self.map_prefix, req.text),
            detected_lang: None,
        })
    }
}
