use crate::{
    check_cancel, ChatMessage, ChatResult, MessageContent, ModelInfo, ProviderConfig,
    ProviderError, Result,
};
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use reqwest::Client;
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;
use tracing::debug;

#[derive(Clone)]
pub struct OpenAiClient {
    http: Client,
    cfg: ProviderConfig,
}

impl OpenAiClient {
    pub fn new(cfg: ProviderConfig) -> Result<Self> {
        let http = Client::builder()
            .timeout(std::time::Duration::from_secs(cfg.timeout_secs))
            .build()?;
        Ok(Self { http, cfg })
    }

    pub fn config(&self) -> &ProviderConfig {
        &self.cfg
    }

    pub async fn chat(
        &self,
        messages: &[ChatMessage],
        temperature: f32,
        cancel: &CancellationToken,
    ) -> Result<ChatResult> {
        check_cancel(cancel).await?;

        let msgs: Vec<Value> = messages
            .iter()
            .map(|m| match &m.content {
                MessageContent::Text(t) => {
                    json!({"role": m.role, "content": t})
                }
                MessageContent::Parts { text, image_png } => {
                    let mut parts = vec![json!({"type": "text", "text": text})];
                    if let Some(png) = image_png {
                        let b64 = B64.encode(png);
                        parts.push(json!({
                            "type": "image_url",
                            "image_url": {
                                "url": format!("data:image/png;base64,{b64}")
                            }
                        }));
                    }
                    json!({"role": m.role, "content": parts})
                }
            })
            .collect();

        let body = json!({
            "model": self.cfg.model,
            "messages": msgs,
            "temperature": temperature,
        });

        let url = format!("{}/chat/completions", self.cfg.base_url);
        debug!(%url, model = %self.cfg.model, "openai chat");

        let mut req = self.http.post(&url).json(&body);
        if !self.cfg.api_key.is_empty() {
            req = req.bearer_auth(&self.cfg.api_key);
        }

        let send = req.send();
        let resp = tokio::select! {
            _ = cancel.cancelled() => return Err(ProviderError::Cancelled),
            r = send => r?,
        };

        let status = resp.status();
        let val: Value = resp.json().await?;
        if !status.is_success() {
            return Err(ProviderError::Api(format!(
                "status {status}: {}",
                val
            )));
        }

        let text = val
            .pointer("/choices/0/message/content")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        if text.is_empty() {
            return Err(ProviderError::Api(format!("empty content: {val}")));
        }

        Ok(ChatResult {
            text,
            raw: Some(val),
        })
    }

    /// `GET {base_url}/models` — OpenAI-compatible model catalog.
    pub async fn list_models(&self, cancel: &CancellationToken) -> Result<Vec<ModelInfo>> {
        check_cancel(cancel).await?;

        let url = format!("{}/models", self.cfg.base_url.trim_end_matches('/'));
        debug!(%url, "openai list models");

        let mut req = self.http.get(&url);
        if !self.cfg.api_key.is_empty() {
            req = req.bearer_auth(&self.cfg.api_key);
        }

        let send = req.send();
        let resp = tokio::select! {
            _ = cancel.cancelled() => return Err(ProviderError::Cancelled),
            r = send => r?,
        };

        let status = resp.status();
        let val: Value = resp.json().await?;
        if !status.is_success() {
            return Err(ProviderError::Api(format!("status {status}: {val}")));
        }

        let out = parse_models_json(&val);
        if out.is_empty() {
            return Err(ProviderError::Api(format!(
                "no models in response: {val}"
            )));
        }
        Ok(out)
    }
}

/// Convenience helper for UI / CLI: fetch model ids from an OpenAI-compatible base URL.
pub async fn fetch_openai_model_ids(
    base_url: &str,
    api_key: &str,
) -> Result<Vec<String>> {
    let cfg = ProviderConfig::openai_compat(base_url, api_key, "");
    let client = OpenAiClient::new(cfg)?;
    let models = client
        .list_models(&CancellationToken::new())
        .await?;
    Ok(models.into_iter().map(|m| m.id).collect())
}

/// Parse OpenAI-style or bare-array model list JSON (unit-testable).
pub fn parse_models_json(val: &Value) -> Vec<ModelInfo> {
    let mut out = Vec::new();
    if let Some(arr) = val.get("data").and_then(|d| d.as_array()) {
        for item in arr {
            if let Some(id) = item.get("id").and_then(|v| v.as_str()) {
                if id.is_empty() {
                    continue;
                }
                out.push(ModelInfo {
                    id: id.to_string(),
                    owned_by: item
                        .get("owned_by")
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string()),
                });
            }
        }
    } else if let Some(arr) = val.as_array() {
        for item in arr {
            if let Some(id) = item.as_str() {
                out.push(ModelInfo {
                    id: id.to_string(),
                    owned_by: None,
                });
            } else if let Some(id) = item.get("id").and_then(|v| v.as_str()) {
                out.push(ModelInfo {
                    id: id.to_string(),
                    owned_by: None,
                });
            }
        }
    }
    out.sort_by(|a, b| a.id.cmp(&b.id));
    out.dedup_by(|a, b| a.id == b.id);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parse_openai_style_list() {
        let v = json!({
            "object": "list",
            "data": [
                {"id": "gpt-4o", "owned_by": "openai"},
                {"id": "gpt-4o-mini", "owned_by": "openai"}
            ]
        });
        let m = parse_models_json(&v);
        assert_eq!(m.len(), 2);
        assert_eq!(m[0].id, "gpt-4o");
        assert_eq!(m[1].id, "gpt-4o-mini");
    }
}
