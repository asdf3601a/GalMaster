//! Capture loop + post-capture pipeline worker (vision-language e2e).

use galmaster_capture::{capture_frame_scaled, CaptureTarget};
use galmaster_core::config::Config;
use galmaster_core::pipeline::{apply_control, GateBundle, PipelineHandle};
use galmaster_core::types::{
    ControlMessage, LatencyBreakdown, PipelineProfileKind, TranslationEvent, UnderstandContext,
};
use galmaster_provider::{LlmSamplingExt, ProviderConfig};
use galmaster_understand::{VisionE2e, VisionE2eOptions, VisionUnderstanding};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::{mpsc, Mutex};
use tokio_util::sync::CancellationToken;
use tracing::{debug, info, warn};

pub async fn run_capture_loop(handle: PipelineHandle, config: Arc<Mutex<Config>>) {
    let mut running_rx = handle.running.subscribe();
    let mut last_err: Option<String> = None;
    loop {
        let running = *running_rx.borrow();
        if !running {
            last_err = None;
            if running_rx.changed().await.is_err() {
                break;
            }
            continue;
        }

        let (fps, target, roi, scale, filter) = {
            let cfg = config.lock().await;
            (
                cfg.capture.target_fps.max(1),
                CaptureTarget::from_config(
                    cfg.capture.window_title_contains.clone(),
                    cfg.capture.match_mode,
                ),
                cfg.capture.roi,
                cfg.capture.clamped_image_scale(),
                cfg.capture.scale_filter,
            )
        };
        let period = Duration::from_millis(1000 / fps as u64);

        // Same scaled crop as the GUI preview → what the VLM sees.
        match capture_frame_scaled(&target, roi, scale, filter) {
            Ok(frame) => {
                if last_err.take().is_some() {
                    // Recovered from capture errors — clear sticky message if idle.
                    let s = handle.status_text();
                    if s.starts_with("Capture error") {
                        handle.set_status("Running — capturing…");
                    }
                }
                handle.frames.push(frame).await;
            }
            Err(e) => {
                let msg = e.to_string();
                if last_err.as_deref() != Some(msg.as_str()) {
                    debug!(error = %e, "capture failed");
                    handle.set_status(format!("Capture error: {msg}"));
                    last_err = Some(msg);
                }
            }
        }

        tokio::time::sleep(period).await;
    }
}

pub async fn run_worker(
    handle: PipelineHandle,
    config: Arc<Mutex<Config>>,
    mut control_rx: mpsc::UnboundedReceiver<ControlMessage>,
) {
    let mut gates = {
        let cfg = config.lock().await;
        GateBundle::from_config(&cfg)
    };
    let mut cancel = CancellationToken::new();
    let mut context_lines: Vec<String> = Vec::new();

    info!("pipeline worker started (vision_e2e)");

    loop {
        tokio::select! {
            Some(msg) = control_rx.recv() => {
                match &msg {
                    ControlMessage::Start => {
                        cancel = CancellationToken::new();
                        context_lines.clear();
                    }
                    ControlMessage::Stop => {
                        cancel.cancel();
                        cancel = CancellationToken::new();
                    }
                    ControlMessage::InjectMockEvent(ev) => {
                        handle.publish_event(ev.clone());
                    }
                    _ => {}
                }
                apply_control(
                    msg,
                    &config,
                    &mut gates,
                    &handle.running,
                    &handle.style,
                    &cancel,
                )
                .await;
            }
            _ = tokio::time::sleep(Duration::from_millis(30)) => {
                let running = *handle.running.borrow();
                if !running {
                    continue;
                }
                let Some(frame) = handle.frames.take().await else {
                    continue;
                };
                let decision = gates.frame.evaluate(&frame);
                if !decision.should_process() {
                    if let Some(msg) = decision.waiting_status() {
                        let s = handle.status_text();
                        if s != msg {
                            handle.set_status(msg);
                        }
                    }
                    continue;
                }

                let cfg = config.lock().await.clone();
                let child = cancel.child_token();

                handle.set_status("Recognizing…");
                match process_vision_e2e(frame, &cfg, &mut gates, &mut context_lines, &child).await {
                    Ok(Some(ev)) => {
                        handle.set_status(format!(
                            "Done — {} ms",
                            ev.latency.total_ms
                        ));
                        handle.publish_event(ev);
                    }
                    Ok(None) => {
                        handle.set_status("Skipped — empty or duplicate");
                    }
                    Err(e) => {
                        warn!(error = %e, "vision_e2e error");
                        handle.set_status(format!("Error: {e}"));
                    }
                }
            }
        }
    }
}

async fn process_vision_e2e(
    frame: galmaster_core::types::Frame,
    cfg: &Config,
    gates: &mut GateBundle,
    context_lines: &mut Vec<String>,
    cancel: &CancellationToken,
) -> anyhow::Result<Option<TranslationEvent>> {
    let t0 = Instant::now();
    let mut latency = LatencyBreakdown::default();

    let mut vision = build_vision(cfg)?;
    let t_ex = Instant::now();
    let previous_lines = if cfg.pipeline.translate.context_active() {
        context_lines.clone()
    } else {
        Vec::new()
    };
    let ctx = UnderstandContext {
        target_lang: cfg.target_lang().to_string(),
        previous_lines,
    };
    let result = vision.understand(&frame, &ctx, cancel).await?;
    latency.extract_ms = t_ex.elapsed().as_millis() as u64;
    latency.translate_ms = 0;

    if result.translated.trim().is_empty()
        && result
            .original
            .as_ref()
            .map(|s| s.trim().is_empty())
            .unwrap_or(true)
    {
        return Ok(None);
    }

    if !gates
        .result
        .accept(result.original.as_deref(), &result.translated)
    {
        return Ok(None);
    }

    if cfg.pipeline.translate.context_active() {
        if let Some(line) = cfg.pipeline.translate.context_mode.format_line(
            result.original.as_deref(),
            &result.translated,
        ) {
            push_context(
                context_lines,
                line,
                cfg.pipeline.translate.max_context_lines,
            );
        }
    } else {
        context_lines.clear();
    }

    latency.total_ms = t0.elapsed().as_millis() as u64;
    Ok(Some(TranslationEvent::now(
        result.original,
        result.translated,
        PipelineProfileKind::VisionE2e,
        latency,
    )))
}

fn push_context(lines: &mut Vec<String>, line: String, max: usize) {
    if line.trim().is_empty() || max == 0 {
        return;
    }
    lines.push(line);
    while lines.len() > max {
        lines.remove(0);
    }
}

fn build_vision(cfg: &Config) -> anyhow::Result<Box<dyn VisionUnderstanding>> {
    let key = Config::resolve_api_key(&cfg.pipeline.vision.api_key);
    if key.is_empty() {
        tracing::warn!(
            provider = %cfg.pipeline.vision.provider,
            "no API key set — requests will omit Authorization / x-api-key (local servers may still work)"
        );
    } else {
        tracing::debug!(
            provider = %cfg.pipeline.vision.provider,
            key_len = key.len(),
            "API key set for vision stage"
        );
    }
    let pcfg = ProviderConfig::openai_compat(
        &cfg.pipeline.vision.base_url,
        key,
        &cfg.pipeline.vision.model,
    );
    let opts = VisionE2eOptions {
        sampling: cfg.pipeline.vision.sampling.clone(),
        structured: cfg.pipeline.vision.structured.clone(),
    };
    debug!(
        model = %cfg.pipeline.vision.model,
        sampling = %opts.sampling.set_fields_summary(),
        structured_repair = opts.structured.repair,
        json_object = opts.structured.json_object,
        "vision e2e options"
    );
    Ok(Box::new(VisionE2e::from_provider_name(
        &cfg.pipeline.vision.provider,
        pcfg,
        opts,
    )?))
}
