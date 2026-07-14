//! Model / API / context / sampling settings widgets for the main settings app.

use eframe::egui;
use galmaster_core::config::{
    Config, ContextMode, LlmSamplingParams, StageProviderConfig, StructuredOutputConfig,
    TranslateStageConfig,
};

/// Single API key field (config `api_key`).
/// OpenAI: `Authorization: Bearer`; Anthropic: `x-api-key`.
pub fn ui_api_key_field(ui: &mut egui::Ui, api_key: &mut Option<String>) {
    let mut key_buf = api_key.clone().unwrap_or_default();
    ui.horizontal(|ui| {
        ui.label("API key").on_hover_text(
            "Saved as api_key in config.toml. OpenAI uses Authorization Bearer; Anthropic uses x-api-key.",
        );
        let resp = ui.add(
            egui::TextEdit::singleline(&mut key_buf)
                .desired_width(220.0)
                .password(true)
                .hint_text("sk-…"),
        );
        if resp.changed() {
            let t = key_buf.trim().to_string();
            *api_key = if t.is_empty() { None } else { Some(t) };
        }
        if ui
            .small_button("Clear")
            .on_hover_text("Clear API key")
            .clicked()
        {
            *api_key = None;
            key_buf.clear();
        }
    });
    if Config::has_api_key(api_key) {
        ui.colored_label(egui::Color32::from_rgb(90, 180, 110), "Key: set");
    } else {
        ui.colored_label(
            egui::Color32::from_rgb(220, 120, 80),
            "Key: missing (cloud APIs need a key)",
        );
    }
}

pub fn ui_structured_output_settings(ui: &mut egui::Ui, structured: &mut StructuredOutputConfig) {
    ui.checkbox(&mut structured.repair, "Retry once if JSON invalid")
        .on_hover_text(
            "When the model returns non-JSON, send one text-only repair request (no re-upload of the image).",
        );
    ui.checkbox(
        &mut structured.json_object,
        "JSON object mode (OpenAI-compatible)",
    )
    .on_hover_text(
        "Sends response_format: json_object. Improves structure reliability on supporting servers; others may return HTTP 400.",
    );
    if structured.json_object {
        ui.small(
            "Some OpenAI-compatible gateways reject response_format — uncheck if you get 400.",
        );
    }
}

/// Convenience when the parent holds a full stage config.
pub fn ui_structured_output_for_stage(ui: &mut egui::Ui, vision: &mut StageProviderConfig) {
    ui_structured_output_settings(ui, &mut vision.structured);
}

pub fn ui_context_settings(ui: &mut egui::Ui, translate: &mut TranslateStageConfig) {
    ui.checkbox(&mut translate.context_enabled, "Keep translation context")
        .on_hover_text(
            "Pass previous subtitle lines into the VLM for disambiguation. Cleared on Start.",
        );
    if translate.context_enabled {
        ui.horizontal(|ui| {
            ui.label("Max lines");
            ui.add(
                egui::DragValue::new(&mut translate.max_context_lines)
                    .range(0..=20)
                    .speed(0.2),
            );
            ui.label("Mode");
            egui::ComboBox::from_id_salt("context_mode")
                .selected_text(match translate.context_mode {
                    ContextMode::Original => "Original",
                    ContextMode::Translated => "Translated",
                    ContextMode::Bilingual => "Bilingual",
                })
                .show_ui(ui, |ui| {
                    ui.selectable_value(
                        &mut translate.context_mode,
                        ContextMode::Bilingual,
                        "Bilingual",
                    );
                    ui.selectable_value(
                        &mut translate.context_mode,
                        ContextMode::Original,
                        "Original",
                    );
                    ui.selectable_value(
                        &mut translate.context_mode,
                        ContextMode::Translated,
                        "Translated",
                    );
                });
        });
        ui.small("Bilingual stores `src → tgt` per line. 0 lines = off. Start clears history.");
    }
}

/// Optional sampling fields: checkbox enables override; unchecked omits the field from the API body.
pub fn ui_sampling_settings(ui: &mut egui::Ui, sampling: &mut LlmSamplingParams) {
    egui::CollapsingHeader::new("Advanced model parameters")
        .default_open(false)
        .show(ui, |ui| {
            ui.small(
                "Unchecked = omit from request (server default). \
                 Official Anthropic requires Max tokens; set it if needed. \
                 top_k / reasoning_effort may be rejected by some endpoints.",
            );

            optional_f32(ui, "Temperature", &mut sampling.temperature, 0.0..=2.0, 0.1);
            optional_f32(ui, "Top-p", &mut sampling.top_p, 0.0..=1.0, 0.9);
            optional_u32(ui, "Top-k", &mut sampling.top_k, 1..=200, 40);
            optional_u32(ui, "Max tokens", &mut sampling.max_tokens, 16..=8192, 512);
            optional_f32(
                ui,
                "Frequency penalty",
                &mut sampling.frequency_penalty,
                -2.0..=2.0,
                0.0,
            );
            optional_f32(
                ui,
                "Presence penalty",
                &mut sampling.presence_penalty,
                -2.0..=2.0,
                0.0,
            );
            optional_i64(ui, "Seed", &mut sampling.seed, 0);

            ui.horizontal(|ui| {
                let mut set = sampling
                    .reasoning_effort
                    .as_ref()
                    .map(|s| !s.is_empty())
                    .unwrap_or(false);
                if ui.checkbox(&mut set, "Reasoning effort").changed() {
                    if set {
                        sampling.reasoning_effort = Some("low".into());
                    } else {
                        sampling.reasoning_effort = None;
                    }
                }
                if set {
                    let current = sampling
                        .reasoning_effort
                        .clone()
                        .unwrap_or_else(|| "low".into());
                    egui::ComboBox::from_id_salt("reasoning_effort")
                        .selected_text(&current)
                        .show_ui(ui, |ui| {
                            for v in ["none", "low", "medium", "high", "xhigh"] {
                                if ui.selectable_label(current == v, v).clicked() {
                                    sampling.reasoning_effort = Some(v.into());
                                }
                            }
                        });
                }
            });
        });
}

fn optional_f32(
    ui: &mut egui::Ui,
    label: &str,
    value: &mut Option<f32>,
    range: std::ops::RangeInclusive<f32>,
    default_when_enabled: f32,
) {
    ui.horizontal(|ui| {
        let mut set = value.is_some();
        if ui.checkbox(&mut set, label).changed() {
            *value = if set {
                Some(default_when_enabled)
            } else {
                None
            };
        }
        if let Some(v) = value.as_mut() {
            ui.add(egui::Slider::new(v, range));
        }
    });
}

fn optional_u32(
    ui: &mut egui::Ui,
    label: &str,
    value: &mut Option<u32>,
    range: std::ops::RangeInclusive<u32>,
    default_when_enabled: u32,
) {
    ui.horizontal(|ui| {
        let mut set = value.is_some();
        if ui.checkbox(&mut set, label).changed() {
            *value = if set {
                Some(default_when_enabled)
            } else {
                None
            };
        }
        if let Some(v) = value.as_mut() {
            ui.add(egui::DragValue::new(v).range(range));
        }
    });
}

fn optional_i64(ui: &mut egui::Ui, label: &str, value: &mut Option<i64>, default_when_enabled: i64) {
    ui.horizontal(|ui| {
        let mut set = value.is_some();
        if ui.checkbox(&mut set, label).changed() {
            *value = if set {
                Some(default_when_enabled)
            } else {
                None
            };
        }
        if let Some(v) = value.as_mut() {
            ui.add(egui::DragValue::new(v));
        }
    });
}
