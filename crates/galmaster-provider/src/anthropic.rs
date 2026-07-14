use crate::{
    apply_anthropic_auth, check_cancel, format_api_error, ApiErrorKind, ChatMessage,
    ChatRequestOptions, ChatResult, LlmSamplingExt, LlmSamplingParams, MessageContent,
    ProviderConfig, ProviderError, Result,
};
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use reqwest::Client;
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;
use tracing::debug;

#[derive(Clone)]
pub struct AnthropicClient {
    http: Client,
    cfg: ProviderConfig,
}

impl AnthropicClient {
    pub fn new(cfg: ProviderConfig) -> Result<Self> {
        let http = Client::builder()
            .timeout(std::time::Duration::from_secs(cfg.timeout_secs))
            .build()?;
        Ok(Self { http, cfg })
    }

    pub async fn chat(
        &self,
        messages: &[ChatMessage],
        params: &LlmSamplingParams,
        opts: &ChatRequestOptions,
        cancel: &CancellationToken,
    ) -> Result<ChatResult> {
        check_cancel(cancel).await?;

        // Anthropic: system is separate; messages are user/assistant.
        // `opts.json_object` is OpenAI-only; ignored here.
        let _ = opts;

        let mut system = String::new();
        let mut api_messages: Vec<Value> = Vec::new();

        for m in messages {
            match m.role.as_str() {
                "system" => {
                    if let MessageContent::Text(t) = &m.content {
                        system = t.clone();
                    }
                }
                role => {
                    let content = match &m.content {
                        MessageContent::Text(t) => json!([{"type": "text", "text": t}]),
                        MessageContent::Parts { text, image_png } => {
                            let mut parts = Vec::new();
                            if let Some(png) = image_png {
                                parts.push(json!({
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": B64.encode(png),
                                    }
                                }));
                            }
                            parts.push(json!({"type": "text", "text": text}));
                            Value::Array(parts)
                        }
                    };
                    api_messages.push(json!({
                        "role": if role == "assistant" { "assistant" } else { "user" },
                        "content": content,
                    }));
                }
            }
        }

        let mut body = json!({
            "model": self.cfg.model,
            "messages": api_messages,
        });
        params.apply_anthropic(&mut body);
        let sampling_summary = params.set_fields_summary();
        if !system.is_empty() {
            body["system"] = json!(system);
        }

        let url = anthropic_messages_url(&self.cfg.base_url);

        debug!(
            %url,
            model = %self.cfg.model,
            has_api_key = self.cfg.has_api_key(),
            sampling = %sampling_summary,
            "anthropic chat"
        );

        // Headers per Anthropic API: x-api-key + anthropic-version (+ content-type).
        let req = apply_anthropic_auth(self.http.post(&url).json(&body), &self.cfg.api_key);

        let send = req.send();
        let resp = tokio::select! {
            _ = cancel.cancelled() => return Err(ProviderError::Cancelled),
            r = send => r?,
        };

        let status = resp.status();
        let val: Value = resp.json().await?;
        if !status.is_success() {
            return Err(ProviderError::Api(format_api_error(
                ApiErrorKind::Anthropic,
                status,
                &val,
                self.cfg.has_api_key(),
                &sampling_summary,
                false,
            )));
        }

        let text = val
            .pointer("/content/0/text")
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
}

/// Resolve Anthropic Messages endpoint from a flexible base URL.
///
/// Accepts `https://api.anthropic.com`, `…/v1`, or a full `…/v1/messages`.
pub fn anthropic_messages_url(base_url: &str) -> String {
    let base = base_url.trim().trim_end_matches('/');
    if base.ends_with("/v1/messages") || base.ends_with("/messages") {
        base.to_string()
    } else if base.contains("/v1") {
        format!("{base}/messages")
    } else {
        format!("{base}/v1/messages")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn messages_url_variants() {
        assert_eq!(
            anthropic_messages_url("https://api.anthropic.com"),
            "https://api.anthropic.com/v1/messages"
        );
        assert_eq!(
            anthropic_messages_url("https://api.anthropic.com/v1"),
            "https://api.anthropic.com/v1/messages"
        );
        assert_eq!(
            anthropic_messages_url("https://api.anthropic.com/v1/"),
            "https://api.anthropic.com/v1/messages"
        );
        assert_eq!(
            anthropic_messages_url("https://api.anthropic.com/v1/messages"),
            "https://api.anthropic.com/v1/messages"
        );
    }
}
