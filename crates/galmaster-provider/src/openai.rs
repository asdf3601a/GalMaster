use crate::{
    apply_openai_auth, check_cancel, ChatMessage, ChatRequestOptions, ChatResult, LlmSamplingParams,
    MessageContent, ModelInfo, ProviderConfig, ProviderError, Result,
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
        params: &LlmSamplingParams,
        opts: &ChatRequestOptions,
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

        let mut body = json!({
            "model": self.cfg.model,
            "messages": msgs,
        });
        params.apply_openai(&mut body);
        opts.apply_openai(&mut body);
        let sampling_summary = params.set_fields_summary();

        let url = join_openai_url(&self.cfg.base_url, "chat/completions");
        debug!(
            %url,
            model = %self.cfg.model,
            has_api_key = self.cfg.has_api_key(),
            sampling = %sampling_summary,
            json_object = opts.json_object,
            "openai chat"
        );

        let req = apply_openai_auth(self.http.post(&url).json(&body), &self.cfg.api_key);

        let send = req.send();
        let resp = tokio::select! {
            _ = cancel.cancelled() => return Err(ProviderError::Cancelled),
            r = send => r?,
        };

        let status = resp.status();
        let val: Value = resp.json().await?;
        if !status.is_success() {
            return Err(ProviderError::Api(format_api_error(
                "openai",
                status,
                &val,
                self.cfg.has_api_key(),
                &sampling_summary,
                opts.json_object,
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

        let url = join_openai_url(&self.cfg.base_url, "models");
        debug!(%url, has_api_key = self.cfg.has_api_key(), "openai list models");

        let req = apply_openai_auth(self.http.get(&url), &self.cfg.api_key);

        let send = req.send();
        let resp = tokio::select! {
            _ = cancel.cancelled() => return Err(ProviderError::Cancelled),
            r = send => r?,
        };

        let status = resp.status();
        let val: Value = resp.json().await?;
        if !status.is_success() {
            return Err(ProviderError::Api(format_api_error(
                "openai",
                status,
                &val,
                self.cfg.has_api_key(),
                "(n/a)",
                false,
            )));
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

/// Join base URL with a relative path without double slashes.
pub fn join_openai_url(base_url: &str, path: &str) -> String {
    let base = base_url.trim().trim_end_matches('/');
    let path = path.trim_start_matches('/');
    format!("{base}/{path}")
}

fn format_api_error(
    provider: &str,
    status: reqwest::StatusCode,
    val: &Value,
    has_api_key: bool,
    sampling_summary: &str,
    json_object: bool,
) -> String {
    let mut msg = format!("status {status}: {val}");
    if status.as_u16() == 401 || status.as_u16() == 403 {
        if !has_api_key {
            msg.push_str(
                " — no API key was sent. Set [pipeline.vision].api_key in config or the GUI.",
            );
        } else {
            msg.push_str(
                " — API rejected the key. Check Authorization: Bearer <key> (OpenAI) and that the key is valid for this base_url.",
            );
        }
    }
    let body_str = val.to_string().to_lowercase();
    if body_str.contains("temperature") {
        msg.push_str(&format!(
            " — sampling sent: [{sampling_summary}]. \
Uncheck Temperature under Advanced model parameters (omit field), or set it to the value this model allows."
        ));
    }
    if json_object
        && (body_str.contains("response_format")
            || body_str.contains("json_object")
            || body_str.contains("response format"))
    {
        msg.push_str(
            " — Uncheck \"JSON object mode\" under Vision model settings if this server does not support response_format.",
        );
    }
    let _ = provider;
    msg
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

    #[test]
    fn join_url_trims_slashes() {
        assert_eq!(
            join_openai_url("https://api.openai.com/v1/", "/chat/completions"),
            "https://api.openai.com/v1/chat/completions"
        );
        assert_eq!(
            join_openai_url("https://api.openai.com/v1", "models"),
            "https://api.openai.com/v1/models"
        );
    }

    #[test]
    fn provider_config_strips_bearer_prefix() {
        let c = ProviderConfig::openai_compat("https://api.openai.com/v1/", "Bearer sk-test", "m");
        assert_eq!(c.api_key, "sk-test");
        assert_eq!(c.base_url, "https://api.openai.com/v1");
        assert!(c.has_api_key());
    }
}
