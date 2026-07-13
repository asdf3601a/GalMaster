mod worker;

use anyhow::Context;
use clap::{Parser, Subcommand};
use galmaster_core::config::Config;
use galmaster_core::pipeline::Pipeline;
use galmaster_core::types::{LatencyBreakdown, PipelineProfileKind, TranslationEvent};
use galmaster_ui::{run_settings_app, AppShared};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tracing::info;
use tracing_subscriber::EnvFilter;

#[derive(Parser, Debug)]
#[command(name = "galmaster", about = "Real-time window subtitle capture & translation")]
struct Cli {
    #[command(subcommand)]
    command: Option<Commands>,

    /// Path to config.toml (default: platform config dir)
    #[arg(long, global = true)]
    config: Option<PathBuf>,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Run GUI (default)
    Run,
    /// Print default config to stdout
    PrintConfig,
    /// Write default config to the default path
    InitConfig,
    /// Headless/desktop functional smoke: OBS + mock subtitles (no API)
    DesktopSmoke {
        /// How long to run (seconds)
        #[arg(long, default_value_t = 12)]
        seconds: u64,
        /// Also open the egui window (needs Wayland/X11)
        #[arg(long, default_value_t = false)]
        with_ui: bool,
        /// Override OBS bind (default from config)
        #[arg(long)]
        bind: Option<String>,
    },
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive("galmaster=info".parse()?))
        .init();

    let cli = Cli::parse();
    let config_path = cli.config.clone();

    match cli.command.unwrap_or(Commands::Run) {
        Commands::PrintConfig => {
            let cfg = Config::default();
            print!("{}", toml::to_string_pretty(&cfg)?);
            return Ok(());
        }
        Commands::InitConfig => {
            let cfg = Config::default();
            let path = cfg.save(config_path.as_deref())?;
            println!("Wrote {}", path.display());
            return Ok(());
        }
        Commands::DesktopSmoke {
            seconds,
            with_ui,
            bind,
        } => {
            return run_desktop_smoke(config_path.as_deref(), seconds, with_ui, bind).await;
        }
        Commands::Run => {}
    }

    let config = Config::load_or_default(config_path.as_deref()).context("load config")?;
    info!(
        path = %config_path
            .clone()
            .unwrap_or_else(Config::default_path)
            .display(),
        "config loaded"
    );

    let (pipeline, control_rx) = Pipeline::create(config.clone());
    let handle = pipeline.handle.clone();
    let config_arc = pipeline.config.clone();

    // OBS server
    let obs_bind = config.obs.bind.clone();
    let obs_events = handle.events.clone();
    let obs_style = handle.style_rx();
    tokio::spawn(async move {
        if let Err(e) = galmaster_obs::serve(&obs_bind, obs_events, obs_style).await {
            tracing::error!(error = %e, "OBS server failed");
        }
    });

    // Capture + pipeline worker
    let worker_handle = handle.clone();
    let worker_cfg = config_arc.clone();
    tokio::spawn(async move {
        worker::run_worker(worker_handle, worker_cfg, control_rx).await;
    });

    // Capture loop (pushes frames when running)
    let cap_handle = handle.clone();
    let cap_cfg = config_arc.clone();
    tokio::spawn(async move {
        worker::run_capture_loop(cap_handle, cap_cfg).await;
    });

    let shared = AppShared {
        config: Arc::new(Mutex::new(config)),
        handle,
        last_event: Arc::new(Mutex::new(None)),
        auto_exit_secs: None,
    };

    // eframe wants to own the main thread on many platforms.
    tokio::task::block_in_place(|| {
        run_settings_app(shared).map_err(|e| anyhow::anyhow!("ui: {e}"))
    })?;

    Ok(())
}

async fn run_desktop_smoke(
    config_path: Option<&std::path::Path>,
    seconds: u64,
    with_ui: bool,
    bind_override: Option<String>,
) -> anyhow::Result<()> {
    let mut config = Config::load_or_default(config_path).context("load config")?;
    if let Some(b) = bind_override {
        config.obs.bind = b;
    }
    // Prefer an ephemeral port if default might be busy in CI re-runs
    if std::env::var_os("GALMASTER_SMOKE_EPHEMERAL").is_some() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
        config.obs.bind = listener.local_addr()?.to_string();
        drop(listener);
    }

    let bind = config.obs.bind.clone();
    println!("desktop-smoke: WAYLAND_DISPLAY={:?}", std::env::var_os("WAYLAND_DISPLAY"));
    println!("desktop-smoke: DISPLAY={:?}", std::env::var_os("DISPLAY"));
    println!("desktop-smoke: XDG_SESSION_TYPE={:?}", std::env::var_os("XDG_SESSION_TYPE"));
    println!("desktop-smoke: OBS http://{bind}/");

    // Capture probe
    match galmaster_capture::list_windows() {
        Ok(ws) => println!("desktop-smoke: list_windows -> {} window(s)", ws.len()),
        Err(e) => println!("desktop-smoke: list_windows error: {e}"),
    }
    let target = galmaster_capture::CaptureTarget::from_config(
        "",
        galmaster_core::config::WindowMatchMode::TitleContains,
    );
    match galmaster_capture::capture_frame(&target, config.capture.roi) {
        Ok(f) => println!(
            "desktop-smoke: capture_frame ok {}x{}",
            f.image.width(),
            f.image.height()
        ),
        Err(e) => println!("desktop-smoke: capture_frame: {e}"),
    }

    let (pipeline, control_rx) = Pipeline::create(config.clone());
    let handle = pipeline.handle.clone();
    let config_arc = pipeline.config.clone();

    let obs_events = handle.events.clone();
    let obs_style = handle.style_rx();
    let bind_s = bind.clone();
    tokio::spawn(async move {
        if let Err(e) = galmaster_obs::serve(&bind_s, obs_events, obs_style).await {
            tracing::error!(error = %e, "OBS server failed");
        }
    });

    let worker_handle = handle.clone();
    let worker_cfg = config_arc.clone();
    tokio::spawn(async move {
        worker::run_worker(worker_handle, worker_cfg, control_rx).await;
    });

    let cap_handle = handle.clone();
    let cap_cfg = config_arc.clone();
    tokio::spawn(async move {
        worker::run_capture_loop(cap_handle, cap_cfg).await;
    });

    // Wait for OBS
    let client = reqwest::Client::new();
    let base = format!("http://{bind}");
    let mut ready = false;
    for _ in 0..100 {
        if let Ok(r) = client.get(format!("{base}/")).send().await {
            if r.status().is_success() {
                ready = true;
                break;
            }
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    anyhow::ensure!(ready, "OBS server not ready at {base}");
    println!("desktop-smoke: OBS index OK");

    let style_txt = client
        .get(format!("{base}/api/style"))
        .send()
        .await?
        .text()
        .await?;
    anyhow::ensure!(style_txt.contains("font"), "style json missing font fields");
    println!("desktop-smoke: OBS /api/style OK");

    // Publish mock subtitles on an interval
    let inject = handle.clone();
    let samples = [
        ("Hello, world.", "你好，世界。"),
        ("This is a subtitle test.", "這是字幕測試。"),
        ("GalMaster on Plasma Wayland.", "GalMaster 於 Plasma Wayland。"),
    ];
    tokio::spawn(async move {
        for i in 0..64u32 {
            let (o, t) = samples[i as usize % samples.len()];
            let ev = TranslationEvent::now(
                Some(o.into()),
                t.into(),
                PipelineProfileKind::ExtractThenTranslate,
                LatencyBreakdown {
                    total_ms: 5 + i as u64,
                    extract_ms: 1,
                    translate_ms: 2,
                    capture_ms: 1,
                },
            );
            inject.publish_event(ev);
            tokio::time::sleep(Duration::from_millis(800)).await;
        }
    });

    if with_ui {
        handle.set_status("desktop-smoke");
        let shared = AppShared {
            config: Arc::new(Mutex::new(config)),
            handle: handle.clone(),
            last_event: Arc::new(Mutex::new(None)),
            auto_exit_secs: Some(seconds.max(3)),
        };
        // winit requires the event loop on the main thread.
        println!("desktop-smoke: launching UI on main thread for {seconds}s (auto-exit)");
        tokio::task::block_in_place(|| {
            run_settings_app(shared).map_err(|e| anyhow::anyhow!("ui: {e}"))
        })?;
        // Final HTTP check after UI closes
        let html = client.get(format!("{base}/")).send().await?.text().await?;
        anyhow::ensure!(html.contains("GalMaster"), "overlay html broken");
        println!("desktop-smoke: PASS (with_ui)");
        return Ok(());
    }

    tokio::time::sleep(Duration::from_secs(seconds.max(3))).await;

    // Final HTTP check after injects
    let html = client.get(format!("{base}/")).send().await?.text().await?;
    anyhow::ensure!(html.contains("GalMaster"), "overlay html broken");
    println!("desktop-smoke: PASS");
    Ok(())
}
