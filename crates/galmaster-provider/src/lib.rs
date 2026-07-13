//! HTTP providers for OpenAI-compatible and Anthropic-compatible APIs.

mod anthropic;
mod openai;

pub use anthropic::AnthropicClient;
pub use openai::{fetch_openai_model_ids, OpenAiClient};

use serde::{Deserialize, Serialize};
use tokio_util::sync::CancellationToken;

#[derive(Debug, Clone)]
pub struct ProviderConfig {
    pub base_url: String,
    pub api_key: String,
    pub model: String,
    pub timeout_secs: u64,
}

impl ProviderConfig {
    pub fn openai_compat(base_url: impl Into<String>, api_key: impl Into<String>, model: impl Into<String>) -> Self {
        Self {
            base_url: base_url.into().trim_end_matches('/').to_string(),
            api_key: api_key.into(),
            model: model.into(),
            timeout_secs: 60,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ChatMessage {
    pub role: String,
    pub content: MessageContent,
}

#[derive(Debug, Clone)]
pub enum MessageContent {
    Text(String),
    /// Multimodal: text + optional PNG/JPEG bytes.
    Parts {
        text: String,
        image_png: Option<Vec<u8>>,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatResult {
    pub text: String,
    pub raw: Option<serde_json::Value>,
}

/// Entry from OpenAI-compatible `GET /v1/models`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelInfo {
    pub id: String,
    #[serde(default)]
    pub owned_by: Option<String>,
}

#[derive(Debug, thiserror::Error)]
pub enum ProviderError {
    #[error("cancelled")]
    Cancelled,
    #[error("http: {0}")]
    Http(#[from] reqwest::Error),
    #[error("api: {0}")]
    Api(String),
    #[error("other: {0}")]
    Other(String),
}

pub type Result<T> = std::result::Result<T, ProviderError>;

pub async fn check_cancel(token: &CancellationToken) -> Result<()> {
    if token.is_cancelled() {
        Err(ProviderError::Cancelled)
    } else {
        Ok(())
    }
}

/// Strip markdown code fences if model wraps JSON.
pub fn strip_code_fence(s: &str) -> String {
    let t = s.trim();
    if let Some(rest) = t.strip_prefix("```") {
        let rest = rest
            .strip_prefix("json")
            .or_else(|| rest.strip_prefix("JSON"))
            .unwrap_or(rest);
        let rest = rest.trim_start_matches('\n');
        if let Some(idx) = rest.rfind("```") {
            return rest[..idx].trim().to_string();
        }
    }
    t.to_string()
}
