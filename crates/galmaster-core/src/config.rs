use crate::style::SubtitleStyle;
use crate::types::NormRect;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Config {
    #[serde(default)]
    pub capture: CaptureConfig,
    #[serde(default)]
    pub pipeline: PipelineConfig,
    #[serde(default)]
    pub gate: GateConfig,
    #[serde(default)]
    pub obs: ObsConfig,
    #[serde(default)]
    pub overlay: OverlayConfig,
    #[serde(default)]
    pub style: SubtitleStyle,
}

/// How to pick the capture target window.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum WindowMatchMode {
    /// Case-insensitive substring of the window title (default).
    #[default]
    TitleContains,
    /// Full window title equals pattern (case-insensitive).
    TitleExact,
    /// Process executable name (e.g. `vlc.exe` / `vlc`) matches pattern.
    Executable,
}

impl WindowMatchMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::TitleContains => "title_contains",
            Self::TitleExact => "title_exact",
            Self::Executable => "executable",
        }
    }

    pub fn from_str_lossy(s: &str) -> Self {
        match s.trim().to_ascii_lowercase().as_str() {
            "title_exact" | "exact" | "same_title" => Self::TitleExact,
            "executable" | "exe" | "process" => Self::Executable,
            _ => Self::TitleContains,
        }
    }
}

/// Image resize filter for ROI crop → preview / VLM.
///
/// Wire names: `nearest` | `bilinear` | `bicubic` | `lanczos`.
/// Older aliases (`triangle`, `catmullrom`, `lanczos3`) still deserialize.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum ScaleFilter {
    /// Nearest neighbor — fast, blocky when scaling up.
    Nearest,
    /// Bilinear (good default).
    #[default]
    #[serde(alias = "triangle")]
    Bilinear,
    /// Bicubic (Catmull-Rom).
    #[serde(alias = "catmullrom", alias = "catmull_rom", alias = "cubic")]
    Bicubic,
    /// Lanczos — sharpest, slowest.
    #[serde(alias = "lanczos3")]
    Lanczos,
}

impl ScaleFilter {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Nearest => "nearest",
            Self::Bilinear => "bilinear",
            Self::Bicubic => "bicubic",
            Self::Lanczos => "lanczos",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Nearest => "Nearest",
            Self::Bilinear => "Bilinear",
            Self::Bicubic => "Bicubic",
            Self::Lanczos => "Lanczos",
        }
    }

    pub fn from_str_lossy(s: &str) -> Self {
        match s.trim().to_ascii_lowercase().as_str() {
            "nearest" | "point" => Self::Nearest,
            "bicubic" | "catmullrom" | "catmull_rom" | "cubic" => Self::Bicubic,
            "lanczos" | "lanczos3" => Self::Lanczos,
            // bilinear + legacy triangle + anything unknown
            _ => Self::Bilinear,
        }
    }

    pub const ALL: [Self; 4] = [
        Self::Nearest,
        Self::Bilinear,
        Self::Bicubic,
        Self::Lanczos,
    ];
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CaptureConfig {
    /// Pattern for match_mode (title text or executable name).
    /// Empty = primary monitor region.
    #[serde(default)]
    pub window_title_contains: String,
    #[serde(default)]
    pub match_mode: WindowMatchMode,
    #[serde(default)]
    pub roi: NormRect,
    /// Pipeline capture rate while running (frames offered to the worker).
    #[serde(default = "default_fps")]
    pub target_fps: u32,
    /// GUI live-preview refresh rate (independent of pipeline FPS).
    /// Lower = less UI lag. Clamped ~0.2–10 in the UI.
    #[serde(default = "default_preview_fps")]
    pub preview_fps: f32,
    /// Scale factor applied after ROI crop for **both** preview and recognition.
    /// `1.0` = native crop size; `0.5` = half resolution (faster / cheaper VLM).
    #[serde(default = "default_image_scale")]
    pub image_scale: f32,
    #[serde(default)]
    pub scale_filter: ScaleFilter,
}

fn default_fps() -> u32 {
    8
}
fn default_preview_fps() -> f32 {
    1.0
}
fn default_image_scale() -> f32 {
    1.0
}

impl Default for CaptureConfig {
    fn default() -> Self {
        Self {
            window_title_contains: String::new(),
            match_mode: WindowMatchMode::default(),
            roi: NormRect::default(),
            target_fps: default_fps(),
            preview_fps: default_preview_fps(),
            image_scale: default_image_scale(),
            scale_filter: ScaleFilter::default(),
        }
    }
}

impl CaptureConfig {
    /// Preview period derived from `preview_fps` (min ~100ms, max ~5s).
    pub fn preview_interval(&self) -> std::time::Duration {
        let fps = self.preview_fps.clamp(0.2, 10.0);
        let ms = (1000.0 / fps).round().clamp(100.0, 5000.0) as u64;
        std::time::Duration::from_millis(ms)
    }

    /// Clamp scale to a safe range used by capture.
    pub fn clamped_image_scale(&self) -> f32 {
        self.image_scale.clamp(0.1, 4.0)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PipelineConfig {
    #[serde(default = "default_profile")]
    pub profile: String,
    #[serde(default)]
    pub extract: StageProviderConfig,
    #[serde(default)]
    pub translate: TranslateStageConfig,
    #[serde(default)]
    pub vision: StageProviderConfig,
}

fn default_profile() -> String {
    // Product default: single VLM call (image → original + translation).
    "vision_e2e".into()
}

impl Default for PipelineConfig {
    fn default() -> Self {
        Self {
            profile: default_profile(),
            extract: StageProviderConfig::default(),
            translate: TranslateStageConfig::default(),
            vision: StageProviderConfig::default(),
        }
    }
}

/// What to store / inject as previous subtitle lines for disambiguation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum ContextMode {
    /// Source text only.
    Original,
    /// Translated text only.
    Translated,
    /// One line: `original → translated` (or just translated if no original).
    #[default]
    Bilingual,
}

impl ContextMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Original => "original",
            Self::Translated => "translated",
            Self::Bilingual => "bilingual",
        }
    }

    pub fn from_str_lossy(s: &str) -> Self {
        match s.trim().to_ascii_lowercase().as_str() {
            "original" | "src" | "source" => Self::Original,
            "translated" | "tgt" | "target" => Self::Translated,
            _ => Self::Bilingual,
        }
    }

    /// Format one history line from a accepted subtitle pair. `None` if empty.
    pub fn format_line(self, original: Option<&str>, translated: &str) -> Option<String> {
        const MAX_CHARS: usize = 200;
        let trunc = |s: &str| -> String {
            let t = s.trim();
            if t.chars().count() <= MAX_CHARS {
                t.to_string()
            } else {
                t.chars().take(MAX_CHARS).collect::<String>() + "…"
            }
        };
        match self {
            Self::Original => {
                let o = original.map(str::trim).filter(|s| !s.is_empty())?;
                Some(trunc(o))
            }
            Self::Translated => {
                let t = translated.trim();
                if t.is_empty() {
                    None
                } else {
                    Some(trunc(t))
                }
            }
            Self::Bilingual => {
                let t = translated.trim();
                let o = original.map(str::trim).filter(|s| !s.is_empty());
                match (o, t.is_empty()) {
                    (Some(o), false) => Some(trunc(&format!("{o} → {t}"))),
                    (Some(o), true) => Some(trunc(o)),
                    (None, false) => Some(trunc(t)),
                    (None, true) => None,
                }
            }
        }
    }
}

/// Optional LLM sampling fields (flattened into stage TOML).
/// Single source of truth; provider applies these as omit-if-unset HTTP body fields.
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reasoning_effort: Option<String>,
}

/// Vision e2e structured-output policy (`[pipeline.vision.structured]`).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct StructuredOutputConfig {
    /// When true (default), retry once with a text-only repair prompt if JSON parse fails.
    #[serde(default = "default_true")]
    pub repair: bool,
    /// When true, OpenAI chat sends `response_format: { type: "json_object" }`.
    #[serde(default)]
    pub json_object: bool,
}

impl Default for StructuredOutputConfig {
    fn default() -> Self {
        Self {
            repair: true,
            json_object: false,
        }
    }
}

/// Trim whitespace, strip mistaken `Bearer ` prefix and surrounding quotes from API keys.
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

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StageProviderConfig {
    #[serde(default = "default_vision_backend")]
    pub backend: String,
    #[serde(default = "default_provider")]
    pub provider: String,
    #[serde(default)]
    pub base_url: String,
    #[serde(default)]
    pub model: String,
    /// Single API key for this stage (OpenAI Bearer / Anthropic x-api-key).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub api_key: Option<String>,
    /// Vision e2e structured-output options (`[pipeline.vision.structured]`).
    #[serde(default)]
    pub structured: StructuredOutputConfig,
    /// Flattened: `temperature`, `top_p`, … live under the same TOML table.
    #[serde(flatten, default)]
    pub sampling: LlmSamplingParams,
}

fn default_vision_backend() -> String {
    "vision_model".into()
}
fn default_provider() -> String {
    "openai_compat".into()
}

impl Default for StageProviderConfig {
    fn default() -> Self {
        Self {
            backend: default_vision_backend(),
            provider: default_provider(),
            base_url: "https://api.openai.com/v1".into(),
            model: "gpt-4o-mini".into(),
            api_key: None,
            structured: StructuredOutputConfig::default(),
            sampling: LlmSamplingParams::default(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TranslateStageConfig {
    #[serde(default = "default_provider")]
    pub backend: String,
    #[serde(default)]
    pub base_url: String,
    #[serde(default)]
    pub model: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub api_key: Option<String>,
    #[serde(default = "default_target_lang")]
    pub target_lang: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_lang: Option<String>,
    /// When false, do not accumulate or send previous subtitle lines.
    #[serde(default = "default_true")]
    pub context_enabled: bool,
    #[serde(default = "default_context_lines")]
    pub max_context_lines: usize,
    #[serde(default)]
    pub context_mode: ContextMode,
    /// Optional sampling for pure-text translate backends (flattened).
    #[serde(flatten, default)]
    pub sampling: LlmSamplingParams,
}

fn default_target_lang() -> String {
    "zh-TW".into()
}
fn default_context_lines() -> usize {
    3
}

impl Default for TranslateStageConfig {
    fn default() -> Self {
        Self {
            backend: default_provider(),
            base_url: "https://api.openai.com/v1".into(),
            model: "gpt-4o-mini".into(),
            api_key: None,
            target_lang: default_target_lang(),
            source_lang: None,
            context_enabled: true,
            max_context_lines: default_context_lines(),
            context_mode: ContextMode::Bilingual,
            sampling: LlmSamplingParams::default(),
        }
    }
}

impl TranslateStageConfig {
    /// Whether the pipeline should inject previous lines into the model prompt.
    pub fn context_active(&self) -> bool {
        self.context_enabled && self.max_context_lines > 0
    }
}

/// Gates for the live vision-e2e path.
///
/// - `pixel_diff_threshold` / `stable_frames` → [`crate::gate::FrameGate`] (ROI stillness)
/// - `text_similarity_skip` → [`crate::gate::ResultGate`] (post-VLM dedup)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GateConfig {
    #[serde(default = "default_pixel_diff")]
    pub pixel_diff_threshold: f32,
    #[serde(default = "default_text_sim")]
    pub text_similarity_skip: f32,
    /// Consecutive similar ROI frames required before a VLM call (frame stillness only).
    #[serde(default = "default_stable_frames")]
    pub stable_frames: u32,
}

fn default_pixel_diff() -> f32 {
    0.01
}
fn default_text_sim() -> f32 {
    0.92
}
fn default_stable_frames() -> u32 {
    2
}

impl Default for GateConfig {
    fn default() -> Self {
        Self {
            pixel_diff_threshold: default_pixel_diff(),
            text_similarity_skip: default_text_sim(),
            stable_frames: default_stable_frames(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ObsConfig {
    #[serde(default = "default_obs_bind")]
    pub bind: String,
}

fn default_obs_bind() -> String {
    "127.0.0.1:8765".into()
}

impl Default for ObsConfig {
    fn default() -> Self {
        Self {
            bind: default_obs_bind(),
        }
    }
}

/// Overlay window backdrop strategy.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum OverlayBackdrop {
    /// Solid chroma-key color (OBS Color Key). Reliable; default.
    #[default]
    Chroma,
    /// Request GL/OS transparency (often unsupported → eframe error).
    Transparent,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OverlayConfig {
    /// Show the floating subtitle overlay window. OBS Browser Source is independent.
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default)]
    pub exclude_from_capture: bool,
    /// Saved outer top-left of the floating overlay (screen pixels).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pos_x: Option<f32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pos_y: Option<f32>,
    /// Overlay window width/height (inner size).
    #[serde(default = "default_overlay_w")]
    pub width: f32,
    #[serde(default = "default_overlay_h")]
    pub height: f32,
    /// `chroma` (default, OBS-friendly) or `transparent` (may fail on Windows GL).
    #[serde(default)]
    pub backdrop: OverlayBackdrop,
    /// Chroma key fill, e.g. `#00FF00` for OBS Color Key.
    #[serde(default = "default_chroma_key")]
    pub chroma_key: String,
}

fn default_true() -> bool {
    true
}

fn default_overlay_w() -> f32 {
    900.0
}
fn default_overlay_h() -> f32 {
    160.0
}
fn default_chroma_key() -> String {
    "#00FF00".into()
}

impl Default for OverlayConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            exclude_from_capture: false,
            pos_x: Some(200.0),
            pos_y: Some(40.0),
            width: default_overlay_w(),
            height: default_overlay_h(),
            backdrop: OverlayBackdrop::Chroma,
            chroma_key: default_chroma_key(),
        }
    }
}

impl Config {
    /// Directory that contains the running executable (portable install root).
    ///
    /// Falls back to the process current directory, then `"."`.
    pub fn config_dir() -> PathBuf {
        if let Ok(exe) = std::env::current_exe() {
            if let Some(parent) = exe.parent() {
                // Prefer canonical path when possible (symlinks / cargo run).
                if let Ok(canon) = parent.canonicalize() {
                    return canon;
                }
                return parent.to_path_buf();
            }
        }
        std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
    }

    /// Default config path: `config.toml` next to the executable.
    pub fn default_path() -> PathBuf {
        Self::config_dir().join("config.toml")
    }

    pub fn load_or_default(path: Option<&Path>) -> anyhow::Result<Self> {
        let path = path
            .map(Path::to_path_buf)
            .unwrap_or_else(Self::default_path);
        if path.exists() {
            let s = std::fs::read_to_string(&path)?;
            let cfg: Config = toml::from_str(&s)?;
            Ok(cfg)
        } else {
            Ok(Config::default())
        }
    }

    pub fn save(&self, path: Option<&Path>) -> anyhow::Result<PathBuf> {
        let path = path
            .map(Path::to_path_buf)
            .unwrap_or_else(Self::default_path);
        if let Some(parent) = path.parent() {
            // Next-to-exe is normally already present; still safe for custom paths.
            let _ = std::fs::create_dir_all(parent);
        }
        let s = toml::to_string_pretty(self)?;
        std::fs::write(&path, s)?;
        Ok(path)
    }

    /// Normalize a raw key string (see free function [`normalize_api_key`]).
    pub fn normalize_api_key(raw: &str) -> String {
        // Call free function explicitly (same name as this method).
        crate::config::normalize_api_key(raw)
    }

    /// Resolve the single stage API key from config (`api_key` only).
    pub fn resolve_api_key(api_key: &Option<String>) -> String {
        match api_key {
            Some(k) => crate::config::normalize_api_key(k),
            None => String::new(),
        }
    }

    /// Whether a non-empty key is configured (for UI status).
    pub fn has_api_key(api_key: &Option<String>) -> bool {
        !Self::resolve_api_key(api_key).is_empty()
    }

    pub fn profile_kind(&self) -> crate::types::PipelineProfileKind {
        // Always vision e2e for the current product surface; other kinds remain
        // in the type system for future ASR / staged pipelines.
        match self.pipeline.profile.as_str() {
            "extract_then_translate" => crate::types::PipelineProfileKind::ExtractThenTranslate,
            "asr_then_translate" => crate::types::PipelineProfileKind::AsrThenTranslate,
            _ => crate::types::PipelineProfileKind::VisionE2e,
        }
    }

    /// Target language for VLM e2e prompts.
    pub fn target_lang(&self) -> &str {
        if !self.pipeline.translate.target_lang.is_empty() {
            &self.pipeline.translate.target_lang
        } else {
            "zh-TW"
        }
    }
}

#[cfg(test)]
mod path_tests {
    use super::*;

    #[test]
    fn default_config_is_beside_exe_dir() {
        let p = Config::default_path();
        assert_eq!(p.file_name().and_then(|s| s.to_str()), Some("config.toml"));
        let exe = std::env::current_exe().expect("current_exe");
        let exe_dir = exe.parent().expect("exe parent");
        let expect = exe_dir
            .canonicalize()
            .unwrap_or_else(|_| exe_dir.to_path_buf())
            .join("config.toml");
        let got = p.canonicalize().unwrap_or(p);
        // File may not exist yet — compare parent dirs.
        assert_eq!(
            Config::config_dir(),
            exe_dir
                .canonicalize()
                .unwrap_or_else(|_| exe_dir.to_path_buf()),
            "expected config next to {}",
            exe.display()
        );
        let _ = (expect, got);
    }

    #[test]
    fn scale_filter_aliases_and_names() {
        for (raw, want) in [
            ("nearest", ScaleFilter::Nearest),
            ("bilinear", ScaleFilter::Bilinear),
            ("triangle", ScaleFilter::Bilinear),
            ("bicubic", ScaleFilter::Bicubic),
            ("catmullrom", ScaleFilter::Bicubic),
            ("lanczos", ScaleFilter::Lanczos),
            ("lanczos3", ScaleFilter::Lanczos),
        ] {
            assert_eq!(ScaleFilter::from_str_lossy(raw), want, "{raw}");
        }
        let toml = r#"scale_filter = "triangle""#;
        #[derive(Deserialize)]
        struct W {
            scale_filter: ScaleFilter,
        }
        let w: W = toml::from_str(toml).unwrap();
        assert_eq!(w.scale_filter, ScaleFilter::Bilinear);
        assert_eq!(ScaleFilter::default().as_str(), "bilinear");
    }

    #[test]
    fn overlay_enabled_defaults_true() {
        let o = OverlayConfig::default();
        assert!(o.enabled);
        // Missing field → default true
        #[derive(Deserialize)]
        struct Wrap {
            #[serde(default)]
            overlay: OverlayConfig,
        }
        let w: Wrap = toml::from_str("").unwrap();
        assert!(w.overlay.enabled);
    }

    #[test]
    fn context_mode_format_and_defaults() {
        assert_eq!(
            ContextMode::Bilingual.format_line(Some("Hello"), "你好").as_deref(),
            Some("Hello → 你好")
        );
        assert_eq!(
            ContextMode::Original.format_line(Some("Hello"), "你好").as_deref(),
            Some("Hello")
        );
        assert_eq!(
            ContextMode::Translated
                .format_line(Some("Hello"), "你好")
                .as_deref(),
            Some("你好")
        );
        assert!(ContextMode::Original.format_line(None, "你好").is_none());

        let t = TranslateStageConfig::default();
        assert!(t.context_enabled);
        assert!(t.context_active());
        assert_eq!(t.context_mode, ContextMode::Bilingual);

        let partial: TranslateStageConfig = toml::from_str(
            r#"
            target_lang = "en"
            context_enabled = false
            "#,
        )
        .unwrap();
        assert!(!partial.context_active());
        assert_eq!(partial.target_lang, "en");
    }

    #[test]
    fn vision_sampling_flatten_toml() {
        let stage: StageProviderConfig = toml::from_str(
            r#"
            provider = "openai_compat"
            base_url = "http://127.0.0.1:8080/v1"
            model = "m"
            temperature = 0.3
            top_p = 0.9
            top_k = 40
            reasoning_effort = "low"
            "#,
        )
        .unwrap();
        assert_eq!(stage.model, "m");
        assert_eq!(stage.sampling.temperature, Some(0.3));
        assert_eq!(stage.sampling.top_p, Some(0.9));
        assert_eq!(stage.sampling.top_k, Some(40));
        assert_eq!(stage.sampling.reasoning_effort.as_deref(), Some("low"));
        assert!(stage.sampling.max_tokens.is_none());
        // nested structured defaults when omitted
        assert!(stage.structured.repair);
        assert!(!stage.structured.json_object);
    }

    #[test]
    fn vision_structured_nested_toml() {
        let stage: StageProviderConfig = toml::from_str(
            r#"
            provider = "openai_compat"
            model = "m"
            temperature = 0.2

            [structured]
            repair = false
            json_object = true
            "#,
        )
        .unwrap();
        assert!(!stage.structured.repair);
        assert!(stage.structured.json_object);
        assert_eq!(stage.sampling.temperature, Some(0.2));
    }

    #[test]
    fn normalize_api_key_strips_bearer_and_quotes() {
        assert_eq!(Config::normalize_api_key("  Bearer sk-abc  "), "sk-abc");
        assert_eq!(Config::normalize_api_key("\"sk-xyz\""), "sk-xyz");
        assert_eq!(Config::normalize_api_key("sk-plain"), "sk-plain");
    }

    #[test]
    fn resolve_api_key_single_field() {
        assert_eq!(
            Config::resolve_api_key(&Some("  Bearer sk-from-config  ".into())),
            "sk-from-config"
        );
        assert_eq!(Config::resolve_api_key(&None), "");
        assert_eq!(Config::resolve_api_key(&Some("".into())), "");
        assert!(Config::has_api_key(&Some("sk-x".into())));
        assert!(!Config::has_api_key(&None));
    }
}
