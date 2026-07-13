use crate::style_editor::{apply_fonts, draw_subtitle_preview, style_editor_ui};
use eframe::egui;
use eframe::egui::{ColorImage, TextureHandle, TextureOptions};
use galmaster_capture::{capture_frame_scaled, list_windows, CaptureTarget, WindowInfo};
use galmaster_core::config::{Config, OverlayBackdrop, ScaleFilter, WindowMatchMode};
use galmaster_core::pipeline::PipelineHandle;
use galmaster_core::types::{ControlMessage, NormRect, TranslationEvent};
use galmaster_provider::fetch_openai_model_ids;
use image::RgbaImage;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// Background result of `GET /models`.
type ModelsFetchSlot = Arc<Mutex<Option<Result<Vec<String>, String>>>>;

/// Background ROI capture for the GUI preview (never blocks the UI thread).
type PreviewSlot = Arc<Mutex<Option<Result<RgbaImage, String>>>>;

/// Shared state between UI thread and async runtime.
pub struct AppShared {
    pub config: Arc<Mutex<Config>>,
    pub handle: PipelineHandle,
    pub last_event: Arc<Mutex<Option<TranslationEvent>>>,
    /// If set, automatically close the main viewport after this many seconds.
    pub auto_exit_secs: Option<u64>,
}

pub fn run_settings_app(shared: AppShared) -> eframe::Result<()> {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([980.0, 720.0])
            .with_title("GalMaster"),
        ..Default::default()
    };
    eframe::run_native(
        "GalMaster",
        options,
        Box::new(|cc| Ok(Box::new(GalMasterApp::new(cc, shared)))),
    )
}

pub struct GalMasterApp {
    shared: AppShared,
    config: Config,
    windows: Vec<WindowInfo>,
    event_rx_poll: Option<tokio::sync::broadcast::Receiver<TranslationEvent>>,
    last_font_key: String,
    running: bool,
    mock_original: String,
    mock_translated: String,
    started_at: Instant,
    auto_exit_secs: Option<u64>,
    /// After first placement, do not re-apply OuterPosition every frame (breaks dragging).
    overlay_position_locked: bool,
    /// Authoritative overlay top-left while / after dragging (avoids outer_rect lag).
    overlay_live_pos: Option<egui::Pos2>,
    overlay_dragging: bool,

    // —— ROI preview (async) ——
    /// Live preview off by default so opening the GUI stays responsive.
    preview_enabled: bool,
    preview_texture: Option<TextureHandle>,
    preview_px: (u32, u32),
    preview_last_request: Instant,
    preview_error: String,
    preview_busy: Arc<AtomicBool>,
    preview_slot: PreviewSlot,
    /// Force one capture on next schedule tick (ROI change / Refresh).
    preview_force: bool,

    // —— Models list ——
    model_ids: Vec<String>,
    models_status: String,
    models_loading: bool,
    models_fetch_slot: ModelsFetchSlot,
    models_fetch_key: String,
    models_manual: bool,
}

fn font_reload_key(style: &galmaster_core::style::SubtitleStyle) -> String {
    format!(
        "{}|{}",
        style.font_family,
        style
            .font_path
            .as_ref()
            .map(|p| p.display().to_string())
            .unwrap_or_default()
    )
}

fn refresh_window_list() -> Vec<WindowInfo> {
    list_windows().unwrap_or_default()
}

impl GalMasterApp {
    pub fn new(cc: &eframe::CreationContext<'_>, shared: AppShared) -> Self {
        let config = shared.config.lock().unwrap().clone();
        apply_fonts(&cc.egui_ctx, &config.style);
        let last_font_key = font_reload_key(&config.style);
        let event_rx_poll = Some(shared.handle.subscribe_events());
        let auto_exit_secs = shared.auto_exit_secs;
        Self {
            shared,
            config,
            windows: refresh_window_list(),
            event_rx_poll,
            last_font_key,
            running: false,
            mock_original: "Hello, world.".into(),
            mock_translated: "你好，世界。".into(),
            started_at: Instant::now(),
            auto_exit_secs,
            overlay_position_locked: false,
            overlay_live_pos: None,
            overlay_dragging: false,
            preview_enabled: false,
            preview_texture: None,
            preview_px: (0, 0),
            preview_last_request: Instant::now() - Duration::from_secs(10),
            preview_error: String::new(),
            preview_busy: Arc::new(AtomicBool::new(false)),
            preview_slot: Arc::new(Mutex::new(None)),
            preview_force: true, // one free grab after start (even if live off)
            model_ids: Vec::new(),
            models_status: "Not loaded".into(),
            models_loading: false,
            models_fetch_slot: Arc::new(Mutex::new(None)),
            models_fetch_key: String::new(),
            models_manual: false,
        }
    }

    fn vision_fetch_key(config: &Config) -> String {
        format!(
            "{}|{}|{}",
            config.pipeline.vision.provider,
            config.pipeline.vision.base_url,
            config.pipeline.vision.api_key_env
        )
    }

    fn poll_models_fetch(&mut self) {
        let taken = {
            let mut g = self.models_fetch_slot.lock().unwrap();
            g.take()
        };
        if let Some(res) = taken {
            self.models_loading = false;
            match res {
                Ok(ids) => {
                    self.model_ids = ids;
                    self.models_status = format!("{} model(s) from API", self.model_ids.len());
                    if self.config.pipeline.vision.model.is_empty() {
                        // Nothing set yet — pick first and show dropdown.
                        self.models_manual = false;
                        if let Some(first) = self.model_ids.first() {
                            self.config.pipeline.vision.model = first.clone();
                        }
                    } else if self
                        .model_ids
                        .iter()
                        .any(|m| m == &self.config.pipeline.vision.model)
                    {
                        self.models_manual = false;
                    } else {
                        // Keep the user's custom model id; switch to manual entry.
                        self.models_manual = true;
                    }
                }
                Err(e) => {
                    self.model_ids.clear();
                    self.models_status = format!("List failed: {e}");
                    self.models_manual = true;
                }
            }
        }
    }

    /// Spawn background `GET {base}/models` for openai_compat endpoints.
    fn request_models_list(&mut self, force: bool) {
        let key = Self::vision_fetch_key(&self.config);
        if !force && key == self.models_fetch_key && (self.models_loading || !self.model_ids.is_empty())
        {
            return;
        }
        self.models_fetch_key = key;

        let provider = self.config.pipeline.vision.provider.clone();
        if !provider.contains("openai") {
            self.models_loading = false;
            self.model_ids.clear();
            self.models_manual = true;
            self.models_status = "Anthropic: enter model id manually".into();
            return;
        }

        let base_url = self.config.pipeline.vision.base_url.clone();
        let api_key = Config::resolve_api_key(
            &self.config.pipeline.vision.api_key,
            &self.config.pipeline.vision.api_key_env,
        );
        if base_url.trim().is_empty() {
            self.models_status = "Set Base URL first".into();
            self.models_manual = true;
            return;
        }

        self.models_loading = true;
        self.models_status = "Fetching /models…".into();
        let slot = Arc::clone(&self.models_fetch_slot);
        *slot.lock().unwrap() = None;

        std::thread::spawn(move || {
            let rt = match tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
            {
                Ok(rt) => rt,
                Err(e) => {
                    *slot.lock().unwrap() = Some(Err(e.to_string()));
                    return;
                }
            };
            let result = rt.block_on(async {
                fetch_openai_model_ids(&base_url, &api_key)
                    .await
                    .map_err(|e| e.to_string())
            });
            *slot.lock().unwrap() = Some(result);
        });
    }

    /// Apply finished background captures to the egui texture (UI thread only).
    fn poll_preview_result(&mut self, ctx: &egui::Context) {
        let taken = {
            let mut g = self.preview_slot.lock().unwrap();
            g.take()
        };
        let Some(res) = taken else {
            return;
        };
        self.preview_busy.store(false, Ordering::SeqCst);
        match res {
            Ok(img) => {
                let w = img.width();
                let h = img.height();
                let color = ColorImage::from_rgba_unmultiplied([w as usize, h as usize], img.as_raw());
                // Nearest when source used nearest; otherwise linear for smoother display.
                let tex_opts = if self.config.capture.scale_filter == ScaleFilter::Nearest {
                    TextureOptions::NEAREST
                } else {
                    TextureOptions::LINEAR
                };
                match self.preview_texture.as_mut() {
                    Some(tex) => tex.set(color, tex_opts),
                    None => {
                        self.preview_texture =
                            Some(ctx.load_texture("roi_capture_preview", color, tex_opts));
                    }
                }
                self.preview_px = (w, h);
                self.preview_error.clear();
            }
            Err(e) => {
                self.preview_error = e;
            }
        }
    }

    /// Schedule a background ROI capture when due. Never blocks the UI thread.
    fn schedule_preview_if_due(&mut self) {
        let force = self.preview_force;
        if !self.preview_enabled && !force {
            return;
        }
        if self.preview_busy.load(Ordering::SeqCst) {
            return;
        }
        let interval = self.config.capture.preview_interval();
        if !force && self.preview_last_request.elapsed() < interval {
            return;
        }

        self.preview_force = false;
        self.preview_last_request = Instant::now();
        self.preview_busy.store(true, Ordering::SeqCst);

        let target = CaptureTarget::from_config(
            self.config.capture.window_title_contains.clone(),
            self.config.capture.match_mode,
        );
        let roi = self.config.capture.roi;
        let scale = self.config.capture.clamped_image_scale();
        let filter = self.config.capture.scale_filter;
        let slot = Arc::clone(&self.preview_slot);
        let busy = Arc::clone(&self.preview_busy);

        std::thread::spawn(move || {
            let result = capture_frame_scaled(&target, roi, scale, filter)
                .map(|f| f.image)
                .map_err(|e| e.to_string());
            *slot.lock().unwrap() = Some(result);
            // Signal completion so repaint loop can pick up even if a previous
            // "busy" check skipped. UI clears busy when applying the texture.
            let _ = busy.load(Ordering::Relaxed);
        });
    }

    fn request_preview_now(&mut self) {
        self.preview_force = true;
        // Allow immediate re-request even if last request was recent.
        self.preview_last_request = Instant::now() - Duration::from_secs(60);
        self.schedule_preview_if_due();
    }

    fn push_config(&mut self) {
        *self.shared.config.lock().unwrap() = self.config.clone();
        let _ = self
            .shared
            .handle
            .control
            .send(ControlMessage::UpdateConfig(Box::new(self.config.clone())));
        self.shared.handle.update_style(self.config.style.clone());
    }

    fn poll_events(&mut self) {
        if let Some(rx) = self.event_rx_poll.as_mut() {
            loop {
                match rx.try_recv() {
                    Ok(ev) => {
                        *self.shared.last_event.lock().unwrap() = Some(ev);
                    }
                    Err(tokio::sync::broadcast::error::TryRecvError::Empty) => break,
                    Err(tokio::sync::broadcast::error::TryRecvError::Lagged(_)) => continue,
                    Err(tokio::sync::broadcast::error::TryRecvError::Closed) => break,
                }
            }
        }
    }

    /// How soon to wake the UI again (avoid 10 Hz busy loop when idle).
    fn schedule_repaint(&self, ctx: &egui::Context) {
        if self.overlay_dragging {
            ctx.request_repaint();
            return;
        }
        let mut after = Duration::from_millis(250);
        if self.preview_enabled || self.preview_busy.load(Ordering::Relaxed) {
            after = after.min(self.config.capture.preview_interval());
        }
        if self.models_loading {
            after = after.min(Duration::from_millis(150));
        }
        if self.running {
            // Poll translation events without spinning.
            after = after.min(Duration::from_millis(200));
        }
        ctx.request_repaint_after(after);
    }
}

impl eframe::App for GalMasterApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.poll_events();
        self.poll_preview_result(ctx);
        self.schedule_preview_if_due();
        self.schedule_repaint(ctx);

        if let Some(secs) = self.auto_exit_secs {
            if self.started_at.elapsed().as_secs() >= secs {
                tracing::info!(secs, "auto-exit: closing UI");
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                return;
            }
        }

        let fk = font_reload_key(&self.config.style);
        if fk != self.last_font_key {
            apply_fonts(ctx, &self.config.style);
            self.last_font_key = fk;
        }

        self.poll_models_fetch();
        let want_key = Self::vision_fetch_key(&self.config);
        if want_key != self.models_fetch_key && !self.models_loading {
            self.request_models_list(false);
        }

        // —— Top bar + status (worker + UI messages share one slot) ——
        egui::TopBottomPanel::top("top").show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.heading("GalMaster");
                ui.separator();
                if ui
                    .button(if self.running { "Stop" } else { "Start" })
                    .on_hover_text("Apply config and start/stop the capture → VLM pipeline")
                    .clicked()
                {
                    self.running = !self.running;
                    self.push_config();
                    self.shared.handle.set_running(self.running);
                }
                if ui
                    .button("Apply")
                    .on_hover_text("Push settings to the running pipeline (without saving disk)")
                    .clicked()
                {
                    self.push_config();
                    self.shared.handle.set_status("Config applied");
                }
                if ui
                    .button("Save config")
                    .on_hover_text("Write config.toml next to the executable")
                    .clicked()
                {
                    self.push_config();
                    match self.config.save(None) {
                        Ok(p) => self
                            .shared
                            .handle
                            .set_status(format!("Saved {}", p.display())),
                        Err(e) => self.shared.handle.set_status(format!("Save error: {e}")),
                    }
                }
                if ui.button("Refresh windows").clicked() {
                    self.windows = refresh_window_list();
                }
            });
            ui.horizontal(|ui| {
                ui.small(egui::RichText::new("Status").weak());
                let status = self.shared.handle.status_text();
                ui.colored_label(status_color(&status), status);
            });
        });

        // —— Left: settings only (no heavy preview work here) ——
        egui::SidePanel::left("left")
            .default_width(360.0)
            .show(ctx, |ui| {
                egui::ScrollArea::vertical().show(ui, |ui| {
                    self.ui_capture_settings(ui);
                    ui.separator();
                    self.ui_vision_settings(ui);
                    ui.separator();
                    self.ui_gate_settings(ui);
                    ui.separator();
                    self.ui_obs_settings(ui);
                    ui.separator();
                    if style_editor_ui(ui, &mut self.config.style) {
                        self.shared.handle.update_style(self.config.style.clone());
                    }
                    ui.separator();
                    self.ui_overlay_settings(ui);
                });
            });

        // —— Center: preview + live output ——
        egui::CentralPanel::default().show(ctx, |ui| {
            egui::ScrollArea::vertical().show(ui, |ui| {
                self.ui_preview_panel(ui);
                ui.separator();
                self.ui_live_subtitle(ui);
                ui.separator();
                self.ui_mock_inject(ui);
                ui.separator();
                ui.label("Tips:");
                ui.small("1. Lower Preview FPS if the GUI lags (capture runs off the UI thread).");
                ui.small("2. image_scale < 1 shrinks both preview and VLM input (cheaper/faster).");
                ui.small("3. Apply then Start — or Start alone also pushes config.");
                ui.small("4. OBS Browser Source → http://127.0.0.1:8765/  ·  Overlay: chroma key.");
            });
        });

        if self.config.overlay.enabled {
            self.show_overlay_viewport(ctx);
        } else {
            // Drop the floating window when disabled so it does not linger.
            let overlay_id = egui::ViewportId::from_hash_of("galmaster_overlay");
            ctx.send_viewport_cmd_to(overlay_id, egui::ViewportCommand::Close);
            self.overlay_dragging = false;
            self.overlay_position_locked = false;
        }
    }
}

// —— UI sections ——
impl GalMasterApp {
    fn ui_capture_settings(&mut self, ui: &mut egui::Ui) {
        ui.heading("Capture");
        ui.horizontal(|ui| {
            ui.label("Match");
            for (label, mode) in [
                ("Title contains", WindowMatchMode::TitleContains),
                ("Same title", WindowMatchMode::TitleExact),
                ("Executable", WindowMatchMode::Executable),
            ] {
                if ui
                    .selectable_label(self.config.capture.match_mode == mode, label)
                    .clicked()
                {
                    self.config.capture.match_mode = mode;
                }
            }
        });
        let pattern_label = match self.config.capture.match_mode {
            WindowMatchMode::TitleContains => "Title contains",
            WindowMatchMode::TitleExact => "Exact title",
            WindowMatchMode::Executable => "Executable (e.g. vlc.exe)",
        };
        ui.label(pattern_label);
        ui.text_edit_singleline(&mut self.config.capture.window_title_contains);

        if !self.windows.is_empty() {
            let selected_short = self
                .config
                .capture
                .window_title_contains
                .chars()
                .take(40)
                .collect::<String>();
            let mut picked_pattern: Option<String> = None;
            egui::ComboBox::from_label("Pick window")
                .selected_text(if selected_short.is_empty() {
                    "(none)".into()
                } else {
                    selected_short
                })
                .show_ui(ui, |ui| {
                    for w in &self.windows {
                        let label = if w.exe_name.is_empty() {
                            w.title.clone()
                        } else {
                            format!("{} [{}]", w.title, w.exe_name)
                        };
                        let is_sel = match self.config.capture.match_mode {
                            WindowMatchMode::Executable => {
                                !w.exe_name.is_empty()
                                    && (w.exe_name.eq_ignore_ascii_case(
                                        &self.config.capture.window_title_contains,
                                    ) || w
                                        .exe_name
                                        .to_lowercase()
                                        .contains(
                                            &self
                                                .config
                                                .capture
                                                .window_title_contains
                                                .to_lowercase(),
                                        ))
                            }
                            WindowMatchMode::TitleExact => {
                                w.title.eq_ignore_ascii_case(
                                    &self.config.capture.window_title_contains,
                                )
                            }
                            WindowMatchMode::TitleContains => w
                                .title
                                .to_lowercase()
                                .contains(&self.config.capture.window_title_contains.to_lowercase()),
                        };
                        if ui.selectable_label(is_sel, label).clicked() {
                            picked_pattern = Some(
                                if self.config.capture.match_mode == WindowMatchMode::Executable {
                                    if w.exe_name.is_empty() {
                                        w.title.clone()
                                    } else {
                                        w.exe_name.clone()
                                    }
                                } else {
                                    w.title.clone()
                                },
                            );
                        }
                    }
                });
            if let Some(pat) = picked_pattern {
                self.config.capture.window_title_contains = pat;
                self.request_preview_now();
            }
        }

        ui.add(
            egui::Slider::new(&mut self.config.capture.target_fps, 1..=30)
                .text("Pipeline FPS")
                .integer(),
        )
        .on_hover_text("How often the running pipeline captures frames for the VLM (not the GUI preview).");

        ui.separator();
        ui.heading("Image scale (preview + VLM)");
        ui.small("Applied after ROI crop. Same pixels for GUI preview and recognition.");
        let scale_before = self.config.capture.image_scale;
        let filter_before = self.config.capture.scale_filter;
        ui.add(
            egui::Slider::new(&mut self.config.capture.image_scale, 0.25..=2.0)
                .text("Scale")
                .fixed_decimals(2),
        );
        ui.horizontal(|ui| {
            if ui.small_button("25%").clicked() {
                self.config.capture.image_scale = 0.25;
            }
            if ui.small_button("50%").clicked() {
                self.config.capture.image_scale = 0.5;
            }
            if ui.small_button("75%").clicked() {
                self.config.capture.image_scale = 0.75;
            }
            if ui.small_button("100%").clicked() {
                self.config.capture.image_scale = 1.0;
            }
            if ui.small_button("150%").clicked() {
                self.config.capture.image_scale = 1.5;
            }
        });
        ui.horizontal(|ui| {
            ui.label("Filter");
            for f in ScaleFilter::ALL {
                if ui
                    .selectable_label(self.config.capture.scale_filter == f, f.label())
                    .clicked()
                {
                    self.config.capture.scale_filter = f;
                }
            }
        });
        if (self.config.capture.image_scale - scale_before).abs() > f32::EPSILON
            || self.config.capture.scale_filter != filter_before
        {
            self.request_preview_now();
        }

        ui.separator();
        ui.label("ROI (normalized 0..1)");
        let roi_before = self.config.capture.roi;
        roi_sliders(ui, &mut self.config.capture.roi);
        if ui.button("Preset: bottom 20%").clicked() {
            self.config.capture.roi = NormRect::default();
        }
        if self.config.capture.roi != roi_before {
            // Debounced: mark force; live rate still applies when busy.
            self.preview_force = true;
            self.preview_last_request = Instant::now() - self.config.capture.preview_interval();
        }
    }

    fn ui_preview_panel(&mut self, ui: &mut egui::Ui) {
        ui.heading("Capture ROI preview");
        ui.horizontal(|ui| {
            let was = self.preview_enabled;
            ui.checkbox(&mut self.preview_enabled, "Live preview")
                .on_hover_text("Off by default. Capture runs on a background thread.");
            if self.preview_enabled && !was {
                self.request_preview_now();
            }
            if ui.button("Refresh now").clicked() {
                self.request_preview_now();
            }
            if self.preview_busy.load(Ordering::Relaxed) {
                ui.spinner();
                ui.small("capturing…");
            }
        });

        ui.add(
            egui::Slider::new(&mut self.config.capture.preview_fps, 0.2..=10.0)
                .text("Preview FPS")
                .fixed_decimals(1),
        )
        .on_hover_text(
            "GUI preview refresh rate only. Lower this if the interface feels laggy. \
             Independent of Pipeline FPS.",
        );
        ui.horizontal(|ui| {
            if ui.small_button("0.5 fps").clicked() {
                self.config.capture.preview_fps = 0.5;
            }
            if ui.small_button("1 fps").clicked() {
                self.config.capture.preview_fps = 1.0;
            }
            if ui.small_button("2 fps").clicked() {
                self.config.capture.preview_fps = 2.0;
            }
            if ui.small_button("5 fps").clicked() {
                self.config.capture.preview_fps = 5.0;
            }
            ui.small(format!(
                "interval ~{} ms",
                self.config.capture.preview_interval().as_millis()
            ));
        });

        let roi = self.config.capture.roi;
        ui.small(format!(
            "roi x={:.2} y={:.2} w={:.2} h={:.2}  ·  scale {:.0}% ({})  ·  pipeline {} fps",
            roi.x,
            roi.y,
            roi.w,
            roi.h,
            self.config.capture.clamped_image_scale() * 100.0,
            self.config.capture.scale_filter.as_str(),
            self.config.capture.target_fps,
        ));

        if !self.preview_error.is_empty() {
            ui.colored_label(egui::Color32::from_rgb(220, 80, 80), &self.preview_error);
            ui.small("Check match mode / window title / display permissions.");
        }

        if let Some(tex) = &self.preview_texture {
            let max_w = ui.available_width().min(860.0);
            let max_h = 320.0;
            let (tw, th) = (
                self.preview_px.0.max(1) as f32,
                self.preview_px.1.max(1) as f32,
            );
            // Fit into pane; allow upscale slightly so tiny crops are readable.
            let fit = (max_w / tw).min(max_h / th).min(4.0);
            let disp = egui::vec2(tw * fit, th * fit);

            egui::Frame::NONE
                .stroke(egui::Stroke::new(1.0, egui::Color32::from_gray(120)))
                .inner_margin(4.0)
                .show(ui, |ui| {
                    ui.image((tex.id(), disp));
                });
            ui.small(format!(
                "Capture buffer {}×{} px  ·  display fit {:.0}%  ·  this is what the VLM receives",
                self.preview_px.0,
                self.preview_px.1,
                fit * 100.0
            ));
        } else if self.preview_error.is_empty() {
            ui.label(
                egui::RichText::new(
                    "No preview yet — enable Live preview or click Refresh now.",
                )
                .weak(),
            );
        }
    }

    fn ui_vision_settings(&mut self, ui: &mut egui::Ui) {
        ui.heading("Vision model (e2e)");
        ui.small("ROI image → VLM → original + translation (single call).");
        self.config.pipeline.profile = "vision_e2e".into();

        ui.group(|ui| {
            let prev_provider = self.config.pipeline.vision.provider.clone();
            let prev_base = self.config.pipeline.vision.base_url.clone();
            let prev_key_env = self.config.pipeline.vision.api_key_env.clone();

            provider_picker(ui, &mut self.config.pipeline.vision.provider);
            labeled_field(ui, "Base URL", &mut self.config.pipeline.vision.base_url);
            labeled_field(
                ui,
                "API key env",
                &mut self.config.pipeline.vision.api_key_env,
            );

            ui.horizontal(|ui| {
                ui.label("Model");
                if !self.model_ids.is_empty() && !self.models_manual {
                    egui::ComboBox::from_id_salt("vision_model_combo")
                        .selected_text(if self.config.pipeline.vision.model.is_empty() {
                            "(select model)"
                        } else {
                            self.config.pipeline.vision.model.as_str()
                        })
                        .width(220.0)
                        .show_ui(ui, |ui| {
                            for id in &self.model_ids {
                                if ui
                                    .selectable_label(self.config.pipeline.vision.model == *id, id)
                                    .clicked()
                                {
                                    self.config.pipeline.vision.model = id.clone();
                                }
                            }
                        });
                } else {
                    ui.add(
                        egui::TextEdit::singleline(&mut self.config.pipeline.vision.model)
                            .desired_width(220.0)
                            .hint_text("model id"),
                    );
                }
            });

            ui.horizontal(|ui| {
                if ui
                    .button(if self.models_loading {
                        "Fetching…"
                    } else {
                        "Refresh models"
                    })
                    .on_hover_text("GET {base_url}/models")
                    .clicked()
                    && !self.models_loading
                {
                    self.request_models_list(true);
                }
                if !self.model_ids.is_empty() {
                    ui.checkbox(&mut self.models_manual, "Manual id")
                        .on_hover_text("Type model id even when a list is available");
                }
            });
            ui.small(&self.models_status);

            labeled_field(
                ui,
                "Target lang",
                &mut self.config.pipeline.translate.target_lang,
            );

            if prev_provider != self.config.pipeline.vision.provider
                || prev_base != self.config.pipeline.vision.base_url
                || prev_key_env != self.config.pipeline.vision.api_key_env
            {
                self.models_fetch_key.clear();
            }
        });
    }

    fn ui_gate_settings(&mut self, ui: &mut egui::Ui) {
        ui.heading("Gates");
        ui.add(
            egui::Slider::new(&mut self.config.gate.pixel_diff_threshold, 0.0..=0.2)
                .text("pixel diff"),
        );
        ui.add(
            egui::Slider::new(&mut self.config.gate.text_similarity_skip, 0.5..=1.0)
                .text("text similarity"),
        );
        ui.add(
            egui::Slider::new(&mut self.config.gate.stable_frames, 1..=5).text("stable frames"),
        );
    }

    fn ui_obs_settings(&mut self, ui: &mut egui::Ui) {
        ui.heading("OBS");
        ui.horizontal(|ui| {
            ui.label("bind");
            ui.text_edit_singleline(&mut self.config.obs.bind);
        });
        ui.small(format!(
            "Browser Source URL: http://{}/",
            self.config.obs.bind
        ));
    }

    fn ui_overlay_settings(&mut self, ui: &mut egui::Ui) {
        ui.heading("Overlay window");
        ui.checkbox(&mut self.config.overlay.enabled, "Show floating overlay")
            .on_hover_text(
                "Desktop subtitle window. Turn off if you only use OBS Browser Source \
                 (http://…:8765/) or the Live panel in this app.",
            );
        if !self.config.overlay.enabled {
            ui.small(egui::RichText::new("Overlay hidden — OBS Browser Source still works.").weak());
            return;
        }
        ui.small(
            "Drag the bar to move. Prefer chroma key for OBS (GL transparency often fails).",
        );
        ui.horizontal(|ui| {
            ui.label("Backdrop");
            for (label, mode) in [
                ("Chroma key (OBS)", OverlayBackdrop::Chroma),
                ("Transparent (may fail)", OverlayBackdrop::Transparent),
            ] {
                if ui
                    .selectable_label(self.config.overlay.backdrop == mode, label)
                    .clicked()
                {
                    self.config.overlay.backdrop = mode;
                }
            }
        });
        if self.config.overlay.backdrop == OverlayBackdrop::Chroma {
            ui.horizontal(|ui| {
                ui.label("Key color");
                let (r, g, b, _) =
                    galmaster_core::style::SubtitleStyle::parse_color(&self.config.overlay.chroma_key);
                let mut rgb = [r, g, b];
                if ui.color_edit_button_rgb(&mut rgb).changed() {
                    self.config.overlay.chroma_key = format!(
                        "#{:02X}{:02X}{:02X}",
                        (rgb[0] * 255.0) as u8,
                        (rgb[1] * 255.0) as u8,
                        (rgb[2] * 255.0) as u8
                    );
                }
                if ui.small_button("Green").clicked() {
                    self.config.overlay.chroma_key = "#00FF00".into();
                }
                if ui.small_button("Magenta").clicked() {
                    self.config.overlay.chroma_key = "#FF00FF".into();
                }
            });
            ui.small(
                "OBS: Window Capture overlay → Filters → Color Key → pick this color.",
            );
        }
        ui.horizontal(|ui| {
            if ui
                .button("Reset overlay position")
                .on_hover_text("Move overlay back to default top area")
                .clicked()
            {
                self.config.overlay.pos_x = Some(200.0);
                self.config.overlay.pos_y = Some(40.0);
                self.overlay_live_pos = Some(egui::pos2(200.0, 40.0));
                self.overlay_position_locked = false;
                self.overlay_dragging = false;
            }
            if let (Some(x), Some(y)) = (self.config.overlay.pos_x, self.config.overlay.pos_y) {
                ui.label(format!("pos ({x:.0}, {y:.0})"));
            }
        });
        ui.checkbox(
            &mut self.config.overlay.exclude_from_capture,
            "Exclude overlay from capture (Windows)",
        );
    }

    fn ui_live_subtitle(&mut self, ui: &mut egui::Ui) {
        ui.heading("Live subtitle");
        let ev = self.shared.last_event.lock().unwrap().clone();
        let (orig, trans, meta) = if let Some(e) = &ev {
            (
                e.original.clone(),
                e.translated.clone(),
                format!(
                    "profile={:?} total={}ms extract={}ms",
                    e.profile, e.latency.total_ms, e.latency.extract_ms
                ),
            )
        } else {
            (None, "(waiting for events…)".into(), String::new())
        };
        draw_subtitle_preview(ui, &self.config.style, orig.as_deref(), &trans);
        ui.small(meta);
    }

    fn ui_mock_inject(&mut self, ui: &mut egui::Ui) {
        ui.heading("Mock inject (no API)");
        ui.horizontal(|ui| {
            ui.label("original");
            ui.text_edit_singleline(&mut self.mock_original);
        });
        ui.horizontal(|ui| {
            ui.label("translated");
            ui.text_edit_singleline(&mut self.mock_translated);
        });
        if ui.button("Inject mock event").clicked() {
            let ev = TranslationEvent::now(
                Some(self.mock_original.clone()),
                self.mock_translated.clone(),
                self.config.profile_kind(),
                Default::default(),
            );
            self.shared.handle.publish_event(ev.clone());
            *self.shared.last_event.lock().unwrap() = Some(ev);
        }
    }
}

impl GalMasterApp {
    fn show_overlay_viewport(&mut self, ctx: &egui::Context) {
        let ev = self.shared.last_event.lock().unwrap().clone();
        let style = self.config.style.clone();
        let ow = self.config.overlay.width.max(280.0);
        let oh = self.config.overlay.height.max(100.0);

        if self.overlay_live_pos.is_none() {
            self.overlay_live_pos = Some(egui::pos2(
                self.config.overlay.pos_x.unwrap_or(200.0),
                self.config.overlay.pos_y.unwrap_or(40.0),
            ));
        }

        let use_chroma = self.config.overlay.backdrop != OverlayBackdrop::Transparent;
        let chroma = {
            let (r, g, b, _) =
                galmaster_core::style::SubtitleStyle::parse_color(&self.config.overlay.chroma_key);
            egui::Color32::from_rgb(
                (r * 255.0) as u8,
                (g * 255.0) as u8,
                (b * 255.0) as u8,
            )
        };

        let mut builder = egui::ViewportBuilder::default()
            .with_title("GalMaster Overlay")
            .with_decorations(false)
            .with_transparent(!use_chroma)
            .with_always_on_top()
            .with_resizable(false)
            .with_taskbar(false);

        if !self.overlay_position_locked {
            if let Some(p) = self.overlay_live_pos {
                builder = builder.with_position(p).with_inner_size([ow, oh]);
            }
        }

        let mut draw_style = style;
        if use_chroma {
            draw_style.background = "#00000000".into();
        }

        let overlay_id = egui::ViewportId::from_hash_of("galmaster_overlay");
        let drag_started = std::cell::Cell::new(false);
        let drag_active = std::cell::Cell::new(false);
        let drag_stopped = std::cell::Cell::new(false);
        let last_outer = std::cell::Cell::new(None::<egui::Pos2>);

        ctx.show_viewport_immediate(overlay_id, builder, |ctx, _class| {
            if let Some(outer) = ctx.input(|i| i.viewport().outer_rect) {
                last_outer.set(Some(outer.min));
            }

            let panel_fill = if use_chroma {
                chroma
            } else {
                egui::Color32::TRANSPARENT
            };

            egui::CentralPanel::default()
                .frame(egui::Frame::NONE.fill(panel_fill))
                .show(ctx, |ui| {
                    let bar_h = 26.0;
                    let bar_w = ui.available_width().max(1.0);
                    let (bar_rect, bar_resp) =
                        ui.allocate_exact_size(egui::vec2(bar_w, bar_h), egui::Sense::drag());
                    let bar_color = if use_chroma {
                        egui::Color32::from_rgb(
                            (chroma.r() as f32 * 0.75) as u8,
                            (chroma.g() as f32 * 0.75) as u8,
                            (chroma.b() as f32 * 0.75) as u8,
                        )
                    } else {
                        egui::Color32::from_rgba_unmultiplied(36, 36, 36, 230)
                    };
                    ui.painter().rect_filled(bar_rect, 4.0, bar_color);
                    ui.painter().text(
                        bar_rect.center(),
                        egui::Align2::CENTER_CENTER,
                        "⠿  drag to move",
                        egui::FontId::proportional(12.0),
                        if use_chroma {
                            egui::Color32::BLACK
                        } else {
                            egui::Color32::from_gray(210)
                        },
                    );

                    if bar_resp.drag_started_by(egui::PointerButton::Primary) {
                        ctx.send_viewport_cmd(egui::ViewportCommand::StartDrag);
                        drag_started.set(true);
                    }
                    if bar_resp.dragged_by(egui::PointerButton::Primary) {
                        drag_active.set(true);
                        ui.ctx().set_cursor_icon(egui::CursorIcon::Grabbing);
                        ui.ctx().request_repaint();
                    } else if bar_resp.hovered() {
                        ui.ctx().set_cursor_icon(egui::CursorIcon::Grab);
                    }
                    if bar_resp.drag_stopped() {
                        drag_stopped.set(true);
                    }

                    let (o, t) = match &ev {
                        Some(e) => (e.original.clone(), e.translated.clone()),
                        None => (None, String::new()),
                    };
                    ui.add_space(6.0);
                    ui.horizontal(|ui| {
                        ui.add_space(8.0);
                        ui.vertical(|ui| {
                            if !t.is_empty()
                                || o.as_ref().map(|s| !s.is_empty()).unwrap_or(false)
                            {
                                draw_subtitle_preview(ui, &draw_style, o.as_deref(), &t);
                            } else {
                                ui.label(
                                    egui::RichText::new("(waiting for subtitles…)")
                                        .color(if use_chroma {
                                            egui::Color32::BLACK
                                        } else {
                                            egui::Color32::GRAY
                                        })
                                        .size(14.0),
                                );
                            }
                        });
                    });
                });
        });

        self.overlay_position_locked = true;

        if drag_started.get() || drag_active.get() {
            self.overlay_dragging = true;
            ctx.request_repaint();
        }

        let released = drag_stopped.get()
            || (self.overlay_dragging && !drag_active.get() && !drag_started.get());
        if released {
            self.overlay_dragging = false;
            if let Some(p) = last_outer.get() {
                self.overlay_live_pos = Some(p);
                self.config.overlay.pos_x = Some(p.x.round());
                self.config.overlay.pos_y = Some(p.y.round());
            }
        } else if let Some(p) = last_outer.get() {
            if !self.overlay_dragging {
                self.overlay_live_pos = Some(p);
            }
        }
    }
}

fn roi_sliders(ui: &mut egui::Ui, roi: &mut NormRect) {
    ui.add(egui::Slider::new(&mut roi.x, 0.0..=1.0).text("x"));
    ui.add(egui::Slider::new(&mut roi.y, 0.0..=1.0).text("y"));
    ui.add(egui::Slider::new(&mut roi.w, 0.05..=1.0).text("w"));
    ui.add(egui::Slider::new(&mut roi.h, 0.05..=1.0).text("h"));
    *roi = roi.clamp();
}

/// Soft color coding for the shared status line (keeps top-bar style simple).
fn status_color(status: &str) -> egui::Color32 {
    let s = status.to_ascii_lowercase();
    if s.starts_with("error") || s.starts_with("save error") || s.starts_with("capture error") {
        egui::Color32::from_rgb(220, 90, 90)
    } else if s.starts_with("done") || s.starts_with("saved") {
        egui::Color32::from_rgb(90, 180, 110)
    } else if s.starts_with("recognizing") || s.starts_with("running") {
        egui::Color32::from_rgb(100, 160, 220)
    } else if s.starts_with("waiting") || s.starts_with("skipped") {
        egui::Color32::from_rgb(200, 170, 80)
    } else if s.starts_with("stopped") {
        egui::Color32::from_gray(140)
    } else {
        egui::Color32::from_gray(190)
    }
}

fn provider_picker(ui: &mut egui::Ui, provider: &mut String) {
    ui.horizontal(|ui| {
        ui.label("Provider");
        for p in ["openai_compat", "anthropic"] {
            if ui.selectable_label(provider.as_str() == p, p).clicked() {
                *provider = p.into();
            }
        }
    });
}

fn labeled_field(ui: &mut egui::Ui, label: &str, value: &mut String) {
    ui.horizontal(|ui| {
        ui.label(label);
        ui.add(egui::TextEdit::singleline(value).desired_width(220.0));
    });
}
