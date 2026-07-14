//! Headless smoke tests (no GUI / no real LLM API).

use galmaster_capture::{capture_frame, crop_norm, list_windows, CaptureTarget};
use galmaster_core::config::WindowMatchMode;
use galmaster_core::config::Config;
use galmaster_core::gate::{FrameGate, ResultGate, TextGate};
use galmaster_core::pipeline::Pipeline;
use galmaster_core::style::SubtitleStyle;
use galmaster_core::types::{
    Frame, LatencyBreakdown, NormRect, PipelineProfileKind, TranslationEvent,
};
use image::{Rgba, RgbaImage};
use std::time::{Duration, Instant};

#[test]
fn config_roundtrip_toml() {
    let cfg = Config::default();
    let s = toml::to_string_pretty(&cfg).expect("serialize");
    let back: Config = toml::from_str(&s).expect("deserialize");
    assert_eq!(back.pipeline.profile, "vision_e2e");
    assert_eq!(back.obs.bind, "127.0.0.1:8765");
    assert!(back.style.font_size_px > 0.0);
    assert!(back.capture.preview_fps > 0.0);
    assert!((back.capture.image_scale - 1.0).abs() < f32::EPSILON);
    assert_eq!(
        back.capture.scale_filter.as_str(),
        "bilinear",
        "default scale filter"
    );
    assert!(back.overlay.enabled, "overlay enabled by default");
}

#[test]
fn style_color_parse() {
    let (r, g, b, a) = SubtitleStyle::parse_color("#FF000080");
    assert!((r - 1.0).abs() < 0.01);
    assert!(g < 0.01 && b < 0.01);
    assert!((a - 128.0 / 255.0).abs() < 0.02);
}

#[test]
fn gates_latest_wins_semantics() {
    let mut fg = FrameGate::new(0.01, 2);
    let mut tg = TextGate::new(0.92, 2);
    let mut rg = ResultGate::new(0.92);

    let f1 = solid(10, 20, 30);
    let f2 = solid(10, 20, 30);
    let f3 = solid(200, 200, 200);
    // Wait for stillness: two similar frames before first process.
    assert!(!fg.should_process(&f1));
    assert!(fg.should_process(&f2));
    assert!(!fg.should_process(&f2));
    // New scene needs two stable frames as well.
    assert!(!fg.should_process(&f3));
    assert!(fg.should_process(&f3));

    assert!(tg.push("hello").is_none());
    assert_eq!(tg.push("hello").as_deref(), Some("hello"));
    assert!(tg.push("hello").is_none());

    assert!(rg.accept(Some("a"), "甲"));
    assert!(!rg.accept(Some("a"), "甲"));
    assert!(rg.accept(Some("b"), "乙"));
}

#[test]
fn crop_roi_bottom_band() {
    let mut img = RgbaImage::new(200, 100);
    for (x, y, p) in img.enumerate_pixels_mut() {
        *p = Rgba([x as u8, y as u8, 0, 255]);
    }
    let roi = NormRect {
        x: 0.05,
        y: 0.78,
        w: 0.90,
        h: 0.18,
    };
    let cropped = crop_norm(&img, roi).unwrap();
    assert_eq!(cropped.width(), 180);
    assert_eq!(cropped.height(), 18);
}

#[test]
fn list_windows_does_not_panic() {
    // May be empty under headless CI; must not crash.
    let _ = list_windows();
}

#[test]
fn capture_primary_or_monitor_frame() {
    // Headless may fail (no compositor); treat success path if available.
    let target = CaptureTarget::from_config("", WindowMatchMode::TitleContains);
    match capture_frame(&target, NormRect::default()) {
        Ok(frame) => {
            assert!(frame.image.width() > 0);
            assert!(frame.image.height() > 0);
            eprintln!(
                "capture ok: {}x{}",
                frame.image.width(),
                frame.image.height()
            );
        }
        Err(e) => {
            eprintln!("capture skipped/failed (expected on some headless hosts): {e}");
        }
    }
}

#[tokio::test]
async fn obs_http_and_ws_style_and_subtitle() {
    let (pipeline, _rx) = Pipeline::create(Config::default());
    let handle = pipeline.handle.clone();
    let events = handle.events.clone();
    let style_rx = handle.style_rx();

    // Bind ephemeral port
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    drop(listener);
    let bind = addr.to_string();

    let events2 = events.clone();
    let style2 = style_rx.clone();
    let bind2 = bind.clone();
    tokio::spawn(async move {
        let _ = galmaster_obs::serve(&bind2, events2, style2).await;
    });

    // Wait for server
    let client = reqwest::Client::new();
    let base = format!("http://{bind}");
    let mut ready = false;
    for _ in 0..50 {
        if client.get(format!("{base}/")).send().await.is_ok() {
            ready = true;
            break;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    assert!(ready, "OBS server did not start");

    let html = client
        .get(format!("{base}/"))
        .send()
        .await
        .unwrap()
        .text()
        .await
        .unwrap();
    assert!(html.contains("GalMaster OBS Overlay"), "index html");

    let style: serde_json::Value = client
        .get(format!("{base}/api/style"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert!(style.get("font_family").is_some());
    assert!(style.get("font_size_px").is_some());

    // Publish a subtitle event — WS clients would receive; HTTP path still serves style.
    let ev = TranslationEvent::now(
        Some("Hello".into()),
        "你好".into(),
        PipelineProfileKind::ExtractThenTranslate,
        LatencyBreakdown {
            total_ms: 12,
            ..Default::default()
        },
    );
    let _ = events.send(ev);

    // Font endpoint without custom font → 404
    let font = client
        .get(format!("{base}/fonts/custom"))
        .send()
        .await
        .unwrap();
    assert_eq!(font.status().as_u16(), 404);
}

#[tokio::test]
async fn pipeline_mock_event_fanout() {
    let (pipeline, mut control_rx) = Pipeline::create(Config::default());
    let mut sub = pipeline.handle.subscribe_events();

    let ev = TranslationEvent::now(
        Some("test".into()),
        "測試".into(),
        PipelineProfileKind::VisionE2e,
        Default::default(),
    );
    pipeline.handle.publish_event(ev.clone());

    let got = tokio::time::timeout(Duration::from_secs(1), sub.recv())
        .await
        .expect("timeout")
        .expect("recv");
    assert_eq!(got.translated, "測試");
    assert_eq!(got.original.as_deref(), Some("test"));

    // Control channel is live
    pipeline.handle.set_running(true);
    let msg = tokio::time::timeout(Duration::from_secs(1), control_rx.recv())
        .await
        .expect("timeout")
        .expect("control");
    match msg {
        galmaster_core::types::ControlMessage::Start => {}
        other => panic!("expected Start, got {other:?}"),
    }
}

fn solid(r: u8, g: u8, b: u8) -> Frame {
    let mut img = RgbaImage::new(32, 16);
    for p in img.pixels_mut() {
        *p = Rgba([r, g, b, 255]);
    }
    Frame {
        image: img,
        captured_at: Instant::now(),
        source_window: None,
        roi: NormRect::default(),
    }
}
