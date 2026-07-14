//! HTTP providers for OpenAI-compatible and Anthropic-compatible APIs.

mod anthropic;
mod openai;

pub use anthropic::AnthropicClient;
pub use openai::{fetch_openai_model_ids, OpenAiClient};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;

#[derive(Debug, Clone)]
pub struct ProviderConfig {
    pub base_url: String,
    pub api_key: String,
    pub model: String,
    pub timeout_secs: u64,
}

impl ProviderConfig {
    pub fn openai_compat(
        base_url: impl Into<String>,
        api_key: impl Into<String>,
        model: impl Into<String>,
    ) -> Self {
        Self {
            base_url: normalize_base_url(base_url.into()),
            api_key: normalize_api_key(api_key.into()),
            model: model.into().trim().to_string(),
            timeout_secs: 60,
        }
    }

    /// Whether an Authorization / x-api-key header will be sent.
    pub fn has_api_key(&self) -> bool {
        !self.api_key.is_empty()
    }
}

/// Trim and strip accidental `Bearer ` prefix from API keys.
pub fn normalize_api_key(raw: impl AsRef<str>) -> String {
    let s = raw.as_ref().trim();
    let s = s
        .strip_prefix("Bearer ")
        .or_else(|| s.strip_prefix("bearer "))
        .unwrap_or(s)
        .trim();
    let s = s
        .strip_prefix('"')
        .and_then(|x| x.strip_suffix('"'))
        .or_else(|| s.strip_prefix('\'').and_then(|x| x.strip_suffix('\'')))
        .unwrap_or(s)
        .trim();
    s.to_string()
}

fn normalize_base_url(raw: String) -> String {
    raw.trim().trim_end_matches('/').to_string()
}

/// OpenAI / OpenAI-compatible auth: `Authorization: Bearer <api_key>`.
///
/// See https://platform.openai.com/docs/api-reference/authentication
pub fn apply_openai_auth(
    mut req: reqwest::RequestBuilder,
    api_key: &str,
) -> reqwest::RequestBuilder {
    let key = normalize_api_key(api_key);
    if !key.is_empty() {
        // Explicit header (equivalent to bearer_auth) so we control the exact value.
        req = req.header(reqwest::header::AUTHORIZATION, format!("Bearer {key}"));
    }
    req
}

/// Anthropic Messages API auth headers.
///
/// Spec: `x-api-key` + required `anthropic-version` (and JSON content-type via body).
/// See https://platform.claude.com/docs/en/api/overview
pub fn apply_anthropic_auth(
    mut req: reqwest::RequestBuilder,
    api_key: &str,
) -> reqwest::RequestBuilder {
    let key = normalize_api_key(api_key);
    req = req
        .header("anthropic-version", "2023-06-01")
        .header(reqwest::header::CONTENT_TYPE, "application/json");
    if !key.is_empty() {
        req = req.header("x-api-key", key);
    }
    req
}

/// Per-request chat options beyond sampling (OpenAI-oriented; Anthropic ignores unknown).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ChatRequestOptions {
    /// When true, OpenAI body includes `response_format: { "type": "json_object" }`.
    /// Anthropic ignores this flag.
    pub json_object: bool,
}

impl ChatRequestOptions {
    pub fn apply_openai(&self, body: &mut Value) {
        if !self.json_object {
            return;
        }
        if let Some(obj) = body.as_object_mut() {
            obj.insert(
                "response_format".into(),
                json!({ "type": "json_object" }),
            );
        }
    }
}

/// Optional sampling / generation parameters for chat completions.
///
/// `None` fields are omitted from the request body (server defaults apply).
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct LlmSamplingParams {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub temperature: Option<f32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub top_p: Option<f32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub top_k: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_tokens: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub frequency_penalty: Option<f32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub presence_penalty: Option<f32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seed: Option<i64>,
    /// OpenAI o-series / compatible gateways: `none` | `low` | `medium` | `high` | …
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reasoning_effort: Option<String>,
}

impl LlmSamplingParams {
    /// Human-readable list of fields that will be sent (empty if none).
    pub fn set_fields_summary(&self) -> String {
        let mut parts = Vec::new();
        if let Some(t) = self.temperature {
            parts.push(format!("temperature={t}"));
        }
        if let Some(p) = self.top_p {
            parts.push(format!("top_p={p}"));
        }
        if let Some(k) = self.top_k {
            parts.push(format!("top_k={k}"));
        }
        if let Some(m) = self.max_tokens {
            parts.push(format!("max_tokens={m}"));
        }
        if let Some(f) = self.frequency_penalty {
            parts.push(format!("frequency_penalty={f}"));
        }
        if let Some(p) = self.presence_penalty {
            parts.push(format!("presence_penalty={p}"));
        }
        if let Some(s) = self.seed {
            parts.push(format!("seed={s}"));
        }
        if let Some(ref r) = self.reasoning_effort {
            if !r.is_empty() {
                parts.push(format!("reasoning_effort={r}"));
            }
        }
        if parts.is_empty() {
            "(none — server defaults)".into()
        } else {
            parts.join(", ")
        }
    }

    /// Apply OpenAI-compatible chat.completions fields (only set keys).
    pub fn apply_openai(&self, body: &mut Value) {
        let obj = match body.as_object_mut() {
            Some(o) => o,
            None => return,
        };
        if let Some(t) = self.temperature {
            obj.insert("temperature".into(), json!(t));
        }
        if let Some(p) = self.top_p {
            obj.insert("top_p".into(), json!(p));
        }
        if let Some(k) = self.top_k {
            // Non-standard for official OpenAI; many compatible servers accept it.
            obj.insert("top_k".into(), json!(k));
        }
        if let Some(m) = self.max_tokens {
            obj.insert("max_tokens".into(), json!(m));
        }
        if let Some(f) = self.frequency_penalty {
            obj.insert("frequency_penalty".into(), json!(f));
        }
        if let Some(p) = self.presence_penalty {
            obj.insert("presence_penalty".into(), json!(p));
        }
        if let Some(s) = self.seed {
            obj.insert("seed".into(), json!(s));
        }
        if let Some(ref r) = self.reasoning_effort {
            if !r.is_empty() {
                obj.insert("reasoning_effort".into(), json!(r));
            }
        }
    }

    /// Apply Anthropic Messages API fields (only set keys).
    ///
    /// Note: official Anthropic requires `max_tokens`; leave it unset only if your
    /// gateway accepts that, or enable Max tokens in Advanced settings.
    pub fn apply_anthropic(&self, body: &mut Value) {
        let obj = match body.as_object_mut() {
            Some(o) => o,
            None => return,
        };
        if let Some(t) = self.temperature {
            obj.insert("temperature".into(), json!(t));
        }
        if let Some(p) = self.top_p {
            obj.insert("top_p".into(), json!(p));
        }
        if let Some(k) = self.top_k {
            obj.insert("top_k".into(), json!(k));
        }
        if let Some(m) = self.max_tokens {
            obj.insert("max_tokens".into(), json!(m));
        }
        // frequency_penalty / presence_penalty / seed / reasoning_effort: not sent
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn openai_omits_unset_fields() {
        let p = LlmSamplingParams {
            temperature: Some(0.2),
            top_p: Some(0.9),
            ..Default::default()
        };
        let mut body = json!({"model": "x", "messages": []});
        p.apply_openai(&mut body);
        assert!((body["temperature"].as_f64().unwrap() - 0.2).abs() < 1e-5);
        assert!((body["top_p"].as_f64().unwrap() - 0.9).abs() < 1e-5);
        assert!(body.get("top_k").is_none());
        assert!(body.get("max_tokens").is_none());
        assert!(body.get("reasoning_effort").is_none());
    }

    #[test]
    fn json_object_mode_optional() {
        let mut body = json!({"model": "x"});
        ChatRequestOptions::default().apply_openai(&mut body);
        assert!(body.get("response_format").is_none());

        ChatRequestOptions {
            json_object: true,
        }
        .apply_openai(&mut body);
        assert_eq!(body["response_format"]["type"], "json_object");
    }

    #[test]
    fn openai_includes_all_set() {
        let p = LlmSamplingParams {
            temperature: Some(0.5),
            top_p: Some(0.95),
            top_k: Some(40),
            max_tokens: Some(256),
            frequency_penalty: Some(0.1),
            presence_penalty: Some(0.2),
            seed: Some(7),
            reasoning_effort: Some("low".into()),
        };
        let mut body = json!({});
        p.apply_openai(&mut body);
        assert_eq!(body["top_k"], 40);
        assert_eq!(body["max_tokens"], 256);
        assert_eq!(body["seed"], 7);
        assert_eq!(body["reasoning_effort"], "low");
    }

    #[test]
    fn anthropic_omits_unset_fields() {
        let p = LlmSamplingParams {
            temperature: Some(0.1),
            ..Default::default()
        };
        let mut body = json!({});
        p.apply_anthropic(&mut body);
        assert!((body["temperature"].as_f64().unwrap() - 0.1).abs() < 1e-5);
        assert!(body.get("max_tokens").is_none());
        assert!(body.get("top_p").is_none());
        assert!(body.get("top_k").is_none());
        assert!(body.get("frequency_penalty").is_none());
        assert!(body.get("reasoning_effort").is_none());
    }

    #[test]
    fn anthropic_includes_max_tokens_when_set() {
        let p = LlmSamplingParams {
            max_tokens: Some(512),
            top_p: Some(0.9),
            ..Default::default()
        };
        let mut body = json!({});
        p.apply_anthropic(&mut body);
        assert_eq!(body["max_tokens"], 512);
        assert!((body["top_p"].as_f64().unwrap() - 0.9).abs() < 1e-5);
    }

    #[test]
    fn omit_all_unset_sampling() {
        let p = LlmSamplingParams::default();
        let mut openai_body = json!({});
        p.apply_openai(&mut openai_body);
        assert_eq!(openai_body, json!({}));

        let mut anthropic_body = json!({});
        p.apply_anthropic(&mut anthropic_body);
        assert_eq!(anthropic_body, json!({}));
    }
}
