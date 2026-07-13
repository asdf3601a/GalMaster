use crate::{
    check_cancel, ChatMessage, ChatResult, MessageContent, ProviderConfig, ProviderError, Result,
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
        temperature: f32,
        cancel: &CancellationToken,
    ) -> Result<ChatResult> {
        check_cancel(cancel).await?;

        // Anthropic: system is separate; messages are user/assistant.
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
            "max_tokens": 1024,
            "temperature": temperature,
        });
        if !system.is_empty() {
            body["system"] = json!(system);
        }

        let url = format!("{}/messages", self.cfg.base_url.trim_end_matches('/'));
        // Allow either full base including /v1 or bare host
        let url = if url.contains("/v1/messages") {
            url
        } else if self.cfg.base_url.contains("/v1") {
            format!("{}/messages", self.cfg.base_url.trim_end_matches('/'))
        } else {
            format!("{}/v1/messages", self.cfg.base_url.trim_end_matches('/'))
        };

        debug!(%url, model = %self.cfg.model, "anthropic chat");

        let mut req = self
            .http
            .post(&url)
            .header("anthropic-version", "2023-06-01")
            .json(&body);
        if !self.cfg.api_key.is_empty() {
            req = req.header("x-api-key", &self.cfg.api_key);
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
