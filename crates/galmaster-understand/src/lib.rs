//! Vision end-to-end: image → original + translated subtitle.

use async_trait::async_trait;
use galmaster_capture::frame_to_png_bytes;
use galmaster_core::config::{LlmSamplingParams, StructuredOutputConfig};
use galmaster_core::types::{Frame, UnderstandContext, UnderstandingResult};
use galmaster_provider::{
    strip_code_fence, ChatClient, ChatMessage, ChatRequestOptions, MessageContent, ProviderConfig,
};
use serde::Deserialize;
use tokio_util::sync::CancellationToken;
use tracing::{debug, warn};

#[async_trait]
pub trait VisionUnderstanding: Send {
    async fn understand(
        &mut self,
        frame: &Frame,
        ctx: &UnderstandContext,
        cancel: &CancellationToken,
    ) -> anyhow::Result<UnderstandingResult>;
}

/// Options for vision e2e (sampling + structured-output policy).
#[derive(Debug, Clone, Default)]
pub struct VisionE2eOptions {
    pub sampling: LlmSamplingParams,
    pub structured: StructuredOutputConfig,
}

pub struct VisionE2e {
    client: ChatClient,
    sampling: LlmSamplingParams,
    structured_repair: bool,
    chat_opts: ChatRequestOptions,
}

impl VisionE2e {
    pub fn new(client: ChatClient, opts: VisionE2eOptions) -> Self {
        Self {
            client,
            sampling: opts.sampling,
            structured_repair: opts.structured.repair,
            chat_opts: ChatRequestOptions {
                json_object: opts.structured.json_object,
            },
        }
    }

    pub fn openai(cfg: ProviderConfig, opts: VisionE2eOptions) -> anyhow::Result<Self> {
        Ok(Self::new(ChatClient::openai(cfg)?, opts))
    }

    pub fn anthropic(cfg: ProviderConfig, opts: VisionE2eOptions) -> anyhow::Result<Self> {
        Ok(Self::new(ChatClient::anthropic(cfg)?, opts))
    }

    pub fn from_provider_name(
        provider: &str,
        cfg: ProviderConfig,
        opts: VisionE2eOptions,
    ) -> anyhow::Result<Self> {
        Ok(Self::new(ChatClient::from_provider_name(provider, cfg)?, opts))
    }

    async fn chat(
        &self,
        messages: &[ChatMessage],
        cancel: &CancellationToken,
    ) -> anyhow::Result<galmaster_provider::ChatResult> {
        Ok(self
            .client
            .chat(messages, &self.sampling, &self.chat_opts, cancel)
            .await?)
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
- Do not add explanations or markdown.
- If previous lines are provided, use them only for disambiguation and pronouns; translate only the current on-screen subtitle, do not repeat history."#
    )
}

fn repair_user_prompt(bad: &str) -> String {
    const MAX: usize = 800;
    let truncated = truncate_chars(bad, MAX);
    format!(
        r#"Your previous reply was not valid JSON. Reply with ONLY one JSON object:
{{"original":"...","translated":"..."}}
No markdown, no explanation.
Previous reply:
{truncated}"#
    )
}

fn truncate_chars(s: &str, max_chars: usize) -> String {
    if s.chars().count() <= max_chars {
        s.to_string()
    } else {
        let t: String = s.chars().take(max_chars).collect();
        format!("{t}…")
    }
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
            user.push_str(
                "\nPrevious lines for context (do not re-translate these; current image only):\n",
            );
            for line in &ctx.previous_lines {
                user.push_str("- ");
                user.push_str(line);
                user.push('\n');
            }
        }

        let system = system_prompt(&ctx.target_lang);
        let messages = [
            ChatMessage {
                role: "system".into(),
                content: MessageContent::Text(system.clone()),
            },
            ChatMessage {
                role: "user".into(),
                content: MessageContent::Parts {
                    text: user,
                    image_png: Some(png),
                },
            },
        ];

        let result = self.chat(&messages, cancel).await?;
        debug!(raw = %result.text, "vision e2e raw");

        if let Some((original, translated)) = parse_e2e(&result.text) {
            return Ok(UnderstandingResult {
                original,
                translated,
                confidence: 0.85,
                raw_model: Some(result.text),
            });
        }

        if self.structured_repair && !cancel.is_cancelled() {
            debug!("vision e2e JSON parse failed; attempting one repair");
            let repair_messages = [
                ChatMessage {
                    role: "system".into(),
                    content: MessageContent::Text(system),
                },
                ChatMessage {
                    role: "user".into(),
                    content: MessageContent::Text(repair_user_prompt(&result.text)),
                },
            ];
            let repaired = self.chat(&repair_messages, cancel).await?;
            debug!(raw = %repaired.text, "vision e2e repair raw");

            if let Some((original, translated)) = parse_e2e(&repaired.text) {
                return Ok(UnderstandingResult {
                    original,
                    translated,
                    confidence: 0.75,
                    raw_model: Some(repaired.text),
                });
            }

            warn!(
                raw = %truncate_chars(&repaired.text, 400),
                "vision e2e structured output invalid after repair; skipping frame"
            );
            return Ok(UnderstandingResult {
                original: None,
                translated: String::new(),
                confidence: 0.0,
                raw_model: Some(repaired.text),
            });
        }

        warn!(
            raw = %truncate_chars(&result.text, 400),
            "vision e2e structured output invalid; skipping frame"
        );
        Ok(UnderstandingResult {
            original: None,
            translated: String::new(),
            confidence: 0.0,
            raw_model: Some(result.text),
        })
    }
}

/// Find the first balanced `{` … `}` slice (string-aware enough for common model junk).
fn extract_json_object(s: &str) -> Option<&str> {
    let bytes = s.as_bytes();
    let start = s.find('{')?;
    let mut depth = 0i32;
    let mut in_string = false;
    let mut escape = false;
    for (i, &b) in bytes.iter().enumerate().skip(start) {
        if in_string {
            if escape {
                escape = false;
            } else if b == b'\\' {
                escape = true;
            } else if b == b'"' {
                in_string = false;
            }
            continue;
        }
        match b {
            b'"' => in_string = true,
            b'{' => depth += 1,
            b'}' => {
                depth -= 1;
                if depth == 0 {
                    return Some(&s[start..=i]);
                }
            }
            _ => {}
        }
    }
    None
}

/// Parse e2e model output. `None` = invalid structured output (do not show as subtitle).
fn parse_e2e(text: &str) -> Option<(Option<String>, String)> {
    let cleaned = strip_code_fence(text);
    try_parse_e2e_json(&cleaned)
        .or_else(|| extract_json_object(&cleaned).and_then(try_parse_e2e_json))
}

fn try_parse_e2e_json(text: &str) -> Option<(Option<String>, String)> {
    let j: E2eJson = serde_json::from_str(text.trim()).ok()?;
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
    Some((original, translated))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_json() {
        let (o, t) = parse_e2e(r#"{"original":"Hello","translated":"你好"}"#).unwrap();
        assert_eq!(o.as_deref(), Some("Hello"));
        assert_eq!(t, "你好");
    }

    #[test]
    fn parse_fenced() {
        let raw = "```json\n{\"original\":\"Hi\",\"translated\":\"嗨\"}\n```";
        let (o, t) = parse_e2e(raw).unwrap();
        assert_eq!(o.as_deref(), Some("Hi"));
        assert_eq!(t, "嗨");
    }

    #[test]
    fn parse_with_prefix_junk() {
        let raw = r#"Here is the result: {"original":"A","translated":"甲"} thanks"#;
        let (o, t) = parse_e2e(raw).unwrap();
        assert_eq!(o.as_deref(), Some("A"));
        assert_eq!(t, "甲");
    }

    #[test]
    fn parse_translation_alias() {
        let (o, t) = parse_e2e(r#"{"original":"X","translation":"Y"}"#).unwrap();
        assert_eq!(o.as_deref(), Some("X"));
        assert_eq!(t, "Y");
    }

    #[test]
    fn parse_empty_ok() {
        let (o, t) = parse_e2e(r#"{"original":"","translated":""}"#).unwrap();
        assert!(o.is_none());
        assert_eq!(t, "");
    }

    #[test]
    fn parse_invalid_returns_none() {
        assert!(parse_e2e("not json at all").is_none());
        assert!(parse_e2e("").is_none());
        assert!(parse_e2e("{broken").is_none());
    }

    #[test]
    fn extract_respects_braces_in_strings() {
        let s = r#"prefix {"original":"a{b}","translated":"c"} tail"#;
        let slice = extract_json_object(s).unwrap();
        assert_eq!(slice, r#"{"original":"a{b}","translated":"c"}"#);
        let (o, t) = parse_e2e(s).unwrap();
        assert_eq!(o.as_deref(), Some("a{b}"));
        assert_eq!(t, "c");
    }
}
