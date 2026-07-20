"""Local HTTP server for OBS Browser Source subtitles (127.0.0.1 only)."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>GalMaster OBS</title>
<style>
  html, body {
    margin: 0; padding: 0;
    width: 100%; height: 100%;
    background: transparent;
    overflow: hidden;
    font-family: "Segoe UI", "Microsoft JhengHei", "Noto Sans CJK TC", sans-serif;
  }
  #wrap {
    box-sizing: border-box;
    width: 100%; min-height: 100%;
    padding: 16px 20px;
    display: flex; flex-direction: column; justify-content: flex-end;
  }
  #panel {
    background: rgba(0, 0, 0, 0.55);
    border-radius: 10px;
    padding: 12px 16px;
    color: #fff;
    text-shadow: 0 1px 2px rgba(0,0,0,0.8);
    max-width: 100%;
    text-align: left;
  }
  #source {
    font-size: 20px;
    color: #d8d8e0;
    opacity: 0.95;
    margin-bottom: 6px;
    white-space: pre-wrap;
    word-break: break-word;
  }
  #source.hidden { display: none; margin: 0; }
  #translation {
    font-size: 28px;
    line-height: 1.35;
    color: #ffffff;
    font-weight: 600;
    white-space: pre-wrap;
    word-break: break-word;
  }
  #translation.hidden { display: none; }
  #translation:empty:not(.hidden)::before {
    content: "…";
    opacity: 0.4;
  }
</style>
</head>
<body>
<div id="wrap">
  <div id="panel">
    <div id="source"></div>
    <div id="translation"></div>
  </div>
</div>
<script>
(function () {
  const elSrc = document.getElementById("source");
  const elTr = document.getElementById("translation");
  const elPanel = document.getElementById("panel");
  let lastJson = "";

  function hexToRgb(hex) {
    hex = (hex || "#000000").replace("#", "");
    if (hex.length === 3) {
      hex = hex.split("").map(c => c + c).join("");
    }
    const n = parseInt(hex, 16);
    if (isNaN(n)) return {r:0,g:0,b:0};
    return {r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255};
  }

  function applyStyle(st) {
    if (!st) return;
    const fam = (st.font_family || "").trim();
    const bodyFont = fam
      ? ('"' + fam.replace(/"/g, '') + '", "Segoe UI", "Microsoft JhengHei", sans-serif')
      : '"Segoe UI", "Microsoft JhengHei", "Noto Sans CJK TC", sans-serif';
    document.body.style.fontFamily = bodyFont;

    const srcSize = parseInt(st.source_font_size, 10) || 20;
    const trSize = parseInt(st.translation_font_size, 10) || 28;
    elSrc.style.fontSize = srcSize + "px";
    elTr.style.fontSize = trSize + "px";
    elSrc.style.color = st.source_color || "#d8d8e0";
    elTr.style.color = st.translation_color || "#ffffff";
    elTr.style.fontWeight = st.translation_bold ? "600" : "400";

    const align = (st.text_align === "center") ? "center" : "left";
    elPanel.style.textAlign = align;

    const bg = hexToRgb(st.bg_color || "#000000");
    let alpha = parseInt(st.bg_alpha, 10);
    if (isNaN(alpha)) alpha = 140;
    alpha = Math.max(0, Math.min(255, alpha)) / 255;
    elPanel.style.background = "rgba(" + bg.r + "," + bg.g + "," + bg.b + "," + alpha + ")";
  }

  function apply(state) {
    if (!state) return;
    const st = state.style || {};
    applyStyle(st);
    const showSrc = st.show_source !== false && st.show_source !== 0 && !!st.show_source;
    // default show_translation true when missing
    const showTr = (st.show_translation === undefined || st.show_translation === null)
      ? true
      : !!(st.show_translation);

    if (showSrc && state.source) {
      elSrc.textContent = state.source;
      elSrc.classList.remove("hidden");
    } else {
      elSrc.textContent = "";
      elSrc.classList.add("hidden");
    }
    if (showTr) {
      elTr.textContent = state.translation || "";
      elTr.classList.remove("hidden");
    } else {
      elTr.textContent = "";
      elTr.classList.add("hidden");
    }
  }

  let pollTimer = null;
  let usePoll = false;

  function stopPoll() {
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    usePoll = false;
  }

  function startPoll() {
    if (usePoll) return;
    usePoll = true;
    function tick() {
      if (!usePoll) return;
      fetch("/api/state", {cache: "no-store"})
        .then(r => r.json())
        .then(st => {
          const j = JSON.stringify(st);
          if (j !== lastJson) {
            lastJson = j;
            apply(st);
          }
        })
        .catch(() => {})
        .finally(() => {
          if (usePoll) pollTimer = setTimeout(tick, 500);
        });
    }
    tick();
  }

  function connectSse() {
    try {
      const es = new EventSource("/events");
      es.onopen = function () {
        // SSE works — stop any poll fallback
        stopPoll();
      };
      es.onmessage = (ev) => {
        try {
          const st = JSON.parse(ev.data);
          lastJson = ev.data;
          apply(st);
        } catch (e) {}
      };
      es.onerror = () => {
        try { es.close(); } catch (e) {}
        // Fall back to poll while reconnecting
        startPoll();
        setTimeout(connectSse, 1500);
      };
    } catch (e) {
      startPoll();
    }
  }

  // Prefer SSE; poll only after EventSource errors (or if construction fails)
  connectSse();
})();
</script>
</body>
</html>
"""


def _default_style() -> dict[str, Any]:
    return {
        "show_source": False,
        "show_translation": True,
        "font_family": "",
        "source_font_size": 20,
        "translation_font_size": 28,
        "source_color": "#d8d8e0",
        "translation_color": "#ffffff",
        "translation_bold": True,
        "text_align": "left",
        "bg_color": "#000000",
        "bg_alpha": 140,
    }


class ObsSubtitleServer:
    """Background ThreadingHTTPServer bound to 127.0.0.1."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._style: dict[str, Any] = _default_style()
        self._state: dict[str, Any] = {
            "source": "",
            "translation": "",
            "status": "",
            "ts": 0.0,
            "style": dict(self._style),
        }
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port = 8765
        self._subscribers: list[threading.Event] = []
        self._error = ""

    @property
    def running(self) -> bool:
        return (
            self._httpd is not None
            and self._thread is not None
            and self._thread.is_alive()
        )

    @property
    def port(self) -> int:
        return self._port

    @property
    def last_error(self) -> str:
        return self._error

    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/"

    def style_dict(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._style)

    def configure(
        self,
        *,
        show_source: bool | None = None,
        show_translation: bool | None = None,
        font_size: int | None = None,
        font_family: str | None = None,
        source_font_size: int | None = None,
        translation_font_size: int | None = None,
        source_color: str | None = None,
        translation_color: str | None = None,
        translation_bold: bool | None = None,
        text_align: str | None = None,
        bg_color: str | None = None,
        bg_alpha: int | None = None,
        **_extra: Any,
    ) -> None:
        with self._lock:
            st = self._style
            if show_source is not None:
                st["show_source"] = bool(show_source)
            if show_translation is not None:
                st["show_translation"] = bool(show_translation)
            if not st.get("show_source") and not st.get("show_translation"):
                st["show_translation"] = True
            if font_family is not None:
                st["font_family"] = str(font_family or "")
            if translation_font_size is not None:
                st["translation_font_size"] = max(
                    10, min(96, int(translation_font_size or 28))
                )
            elif font_size is not None:
                st["translation_font_size"] = max(10, min(96, int(font_size or 28)))
            if source_font_size is not None:
                st["source_font_size"] = max(10, min(96, int(source_font_size or 20)))
            if source_color is not None:
                st["source_color"] = str(source_color or "#d8d8e0")
            if translation_color is not None:
                st["translation_color"] = str(translation_color or "#ffffff")
            if translation_bold is not None:
                st["translation_bold"] = bool(translation_bold)
            if text_align is not None:
                a = str(text_align or "left").lower()
                st["text_align"] = a if a in ("left", "center") else "left"
            if bg_color is not None:
                st["bg_color"] = str(bg_color or "#000000")
            if bg_alpha is not None:
                st["bg_alpha"] = max(0, min(255, int(bg_alpha)))
            # push style into state and notify subscribers
            self._state = {
                **self._state,
                "style": dict(st),
                "ts": time.time(),
            }
            for ev in list(self._subscribers):
                ev.set()

    def publish(
        self,
        *,
        source: str = "",
        translation: str = "",
        status: str = "",
    ) -> None:
        with self._lock:
            self._state = {
                "source": source or "",
                "translation": translation or "",
                "status": status or "",
                "ts": time.time(),
                "style": dict(self._style),
            }
            for ev in list(self._subscribers):
                ev.set()

    def start(self, port: int = 8765) -> None:
        port = max(1, min(65535, int(port or 8765)))
        if self.running and self._port == port:
            self._error = ""
            return
        self.stop()
        self._port = port
        self._error = ""
        server_ref = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return  # quiet

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path or "/"
                if path in ("/", "/index.html", "/obs"):
                    self._serve_html()
                elif path == "/api/state":
                    self._serve_json()
                elif path == "/events":
                    self._serve_sse()
                else:
                    self.send_error(404)

            def _serve_html(self) -> None:
                data = _HTML_TEMPLATE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _serve_json(self) -> None:
                with server_ref._lock:
                    body = json.dumps(server_ref._state, ensure_ascii=False)
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _serve_sse(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                ev = threading.Event()
                with server_ref._lock:
                    server_ref._subscribers.append(ev)
                    payload = json.dumps(server_ref._state, ensure_ascii=False)
                try:
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
                    while server_ref.running:
                        if ev.wait(timeout=15.0):
                            ev.clear()
                            with server_ref._lock:
                                payload = json.dumps(
                                    server_ref._state, ensure_ascii=False
                                )
                            self.wfile.write(f"data: {payload}\n\n".encode())
                            self.wfile.flush()
                        else:
                            # keepalive comment
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with server_ref._lock:
                        if ev in server_ref._subscribers:
                            server_ref._subscribers.remove(ev)

        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            httpd.daemon_threads = True
        except OSError as exc:
            self._error = str(exc)
            self._httpd = None
            self._thread = None
            return

        self._httpd = httpd

        def _run() -> None:
            try:
                httpd.serve_forever(poll_interval=0.5)
            except Exception:
                pass

        t = threading.Thread(target=_run, name="ObsSubtitleServer", daemon=True)
        self._thread = t
        t.start()

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        with self._lock:
            for ev in list(self._subscribers):
                ev.set()
            self._subscribers.clear()
        t = self._thread
        self._thread = None
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._error = ""
