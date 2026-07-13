//! Local HTTP + WebSocket server for OBS Browser Source.

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::State;
use axum::response::{Html, IntoResponse, Response};
use axum::routing::get;
use axum::Json;
use axum::Router;
use futures::{SinkExt, StreamExt};
use galmaster_core::style::SubtitleStyle;
use galmaster_core::types::TranslationEvent;
use serde::Serialize;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tokio::sync::{broadcast, watch};
use tower_http::cors::CorsLayer;
use tracing::{info, warn};

const DEFAULT_HTML: &str = include_str!("../../../assets/obs_overlay.html");

#[derive(Clone)]
pub struct ObsState {
    pub events: broadcast::Sender<TranslationEvent>,
    pub style: watch::Receiver<SubtitleStyle>,
    pub font_path: Arc<tokio::sync::RwLock<Option<PathBuf>>>,
}

#[derive(Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum WsOut {
    Subtitle {
        original: Option<String>,
        translated: String,
        ts: u64,
        profile: String,
    },
    Style {
        style: SubtitleStyle,
        font_url: Option<String>,
    },
    Hello {
        message: String,
    },
}

pub async fn serve(
    bind: &str,
    events: broadcast::Sender<TranslationEvent>,
    style: watch::Receiver<SubtitleStyle>,
) -> anyhow::Result<()> {
    let addr: SocketAddr = bind.parse()?;
    let font_path = Arc::new(tokio::sync::RwLock::new(
        style.borrow().font_path.clone(),
    ));

    let state = ObsState {
        events,
        style,
        font_path,
    };

    let app = Router::new()
        .route("/", get(index))
        .route("/api/style", get(get_style))
        .route("/ws", get(ws_handler))
        .route("/fonts/custom", get(font_handler))
        .layer(CorsLayer::permissive())
        .with_state(state);

    info!(%addr, "OBS Browser Source server listening");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

async fn index() -> Html<&'static str> {
    Html(DEFAULT_HTML)
}

async fn get_style(State(state): State<ObsState>) -> Json<SubtitleStyle> {
    Json(state.style.borrow().clone())
}

async fn font_handler(State(state): State<ObsState>) -> Response {
    let path = state.font_path.read().await.clone();
    match path {
        Some(p) if p.exists() => match tokio::fs::read(&p).await {
            Ok(bytes) => {
                let mime = mime_for_font(&p);
                (
                    [(axum::http::header::CONTENT_TYPE, mime)],
                    bytes,
                )
                    .into_response()
            }
            Err(e) => {
                warn!(error = %e, "read font");
                (axum::http::StatusCode::NOT_FOUND, "font unreadable").into_response()
            }
        },
        _ => (axum::http::StatusCode::NOT_FOUND, "no custom font").into_response(),
    }
}

fn mime_for_font(p: &Path) -> &'static str {
    match p
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_lowercase()
        .as_str()
    {
        "otf" => "font/otf",
        "ttc" => "font/collection",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        _ => "font/ttf",
    }
}

async fn ws_handler(ws: WebSocketUpgrade, State(state): State<ObsState>) -> impl IntoResponse {
    ws.on_upgrade(move |socket| client_loop(socket, state))
}

async fn client_loop(socket: WebSocket, state: ObsState) {
    let (mut sink, mut stream) = socket.split();
    let mut ev_rx = state.events.subscribe();
    let mut style_rx = state.style.clone();

    let hello = WsOut::Hello {
        message: "galmaster obs connected".into(),
    };
    if send_json(&mut sink, &hello).await.is_err() {
        return;
    }

    // Initial style
    {
        let style = style_rx.borrow().clone();
        *state.font_path.write().await = style.font_path.clone();
        let font_url = style
            .font_path
            .as_ref()
            .map(|_| "/fonts/custom".to_string());
        let msg = WsOut::Style { style, font_url };
        if send_json(&mut sink, &msg).await.is_err() {
            return;
        }
    }

    loop {
        tokio::select! {
            msg = stream.next() => {
                match msg {
                    Some(Ok(Message::Close(_))) | None => break,
                    Some(Ok(Message::Ping(p))) => {
                        let _ = sink.send(Message::Pong(p)).await;
                    }
                    _ => {}
                }
            }
            ev = ev_rx.recv() => {
                match ev {
                    Ok(e) => {
                        let msg = WsOut::Subtitle {
                            original: e.original,
                            translated: e.translated,
                            ts: e.ts_unix_ms,
                            profile: format!("{:?}", e.profile),
                        };
                        if send_json(&mut sink, &msg).await.is_err() {
                            break;
                        }
                    }
                    Err(broadcast::error::RecvError::Lagged(_)) => continue,
                    Err(_) => break,
                }
            }
            Ok(()) = style_rx.changed() => {
                let style = style_rx.borrow().clone();
                *state.font_path.write().await = style.font_path.clone();
                let font_url = style.font_path.as_ref().map(|_| "/fonts/custom".to_string());
                let msg = WsOut::Style { style, font_url };
                if send_json(&mut sink, &msg).await.is_err() {
                    break;
                }
            }
        }
    }
}

async fn send_json<T: Serialize>(
    sink: &mut (impl SinkExt<Message> + Unpin),
    val: &T,
) -> Result<(), ()> {
    let s = serde_json::to_string(val).map_err(|_| ())?;
    sink.send(Message::Text(s.into())).await.map_err(|_| ())
}
