# GalMaster Architecture

## Overview

GalMaster is a Windows desktop tool that captures a game window (or screen region), runs OCR or a vision LLM (VLM), optionally translates via an OpenAI/Anthropic-compatible API, and presents results in a main window, a floating Overlay, and optionally an OBS Browser Source.

**Stack:** Python 3.12+, PySide6, Pillow, mss / Win32 GDI / Windows Graphics Capture (WGC).

## Module map

```
app/
  main.py                 # QApplication entry
  app_controller.py       # Wire UI, hotkeys, monitor, capture, pipeline, present
  session/
    capture_stage.py      # Explicit Capture-stage state (Idle / Capturing)
  config.py               # AppConfig load/save (project-root config.json)
  pipeline.py             # Process stage: OCR|VLM → LLM on QThread
  pipeline_queue.py       # Bounded FIFO for Process waiting jobs
  capture/                # Detect + grab (monitor, screenshot, WGC, DPI, windows)
  ocr/                    # OneOCR / Manga / Rapid / Paddle
  translate/              # LLM client, cache, provider presets
  obs/                    # Local HTTP + SSE subtitle server
  ui/                     # MainWindow, Overlay, region selector
  hotkeys/                # Global hotkey filter
  i18n/                   # zh-Hant / en strings
```

### Dependency rules (target)

| Module | May depend on | Should not depend on |
|--------|---------------|----------------------|
| `config` | stdlib | UI, OCR engines, pipeline |
| `capture` | Win32 / mss / WGC, config DTO | UI, LLM, pipeline |
| `ocr` / `translate` | images / text APIs | UI, controller |
| `pipeline` | ocr, translate, **pre-captured Image** | UI; ideally no capture |
| `session` | pure state helpers | Qt widgets (except types if needed) |
| `app_controller` | services + signals | form field details |
| `ui` | config, i18n, signals | capture threads / OCR workers |

## Pipeline stages

```
Detect (RegionMonitor)
    → Capture (CaptureStage + background thread)
        → Process (TranslationPipeline + bounded queue)
            → Present (main UI / Overlay / OBS)
```

| Stage | Owner | Threading | Notes |
|-------|--------|-----------|--------|
| **Detect** | `RegionMonitor` | Daemon thread + Qt signals | Polls region; emits `region_changed` after stable/cooldown |
| **Capture** | `CaptureStage` + `AppController` | One daemon capture thread at a time | Cloaks Overlay when screen capture may include it |
| **Process** | `TranslationPipeline` | Single `QThread` worker | Serial jobs; waiting queue is bounded |
| **Present** | `AppController._present` | UI thread | Soft failures keep last good text |

### Capture vs Process buffering

Two different “busy” layers:

1. **Capture deferred** (`CaptureStage`): at most one grab runs at a time. If another request arrives while capturing, force marks “recapture next”; auto increments a deferred counter capped by `pipeline_buffer_size`.
2. **Process queue** (`pipeline_queue.enqueue_job`): while the worker is busy, jobs wait in a FIFO of capacity `pipeline_buffer_size`. `force=True` clears waiting auto jobs and keeps one force job.

**Authoritative backlog for “how many OCR/translate jobs are waiting”** is the Process queue (`queue_depth`). Capture deferred only means “schedule another grab after the current grab finishes.”

### force vs auto

| Path | `force` | Behavior |
|------|---------|----------|
| Button / hotkey / tray | `True` | Always continue even if text unchanged; clear waiting auto Process jobs; prefer over deferred auto captures |
| Auto-monitor | `False` | May skip unchanged OCR/VLM frame; may queue while Process busy |

## Monitor capture method

Auto-monitor polling intentionally prefers **BitBlt** when method is `auto` (lighter than WGC every interval). Full **WGC / auto** path is used for the actual translation capture. User can force `wgc` for monitor polls too.

## Config location

`config.json` next to the tool root (same folder as `start.bat`). One-time migration from `%APPDATA%\GalMaster\config.json` if missing.

## Related docs

- [State machine](state-machine.md) — events and transitions
- [README](../README.md) — user-facing setup and features
