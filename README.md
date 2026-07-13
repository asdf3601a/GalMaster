# GalMaster

Real-time **window/ROI capture → subtitle extract → translate → overlay / OBS**, written in Rust.

## Principles

1. **Human control stops at ROI** — pick a window and box the subtitle band.
2. **Everything after the screenshot can be a model** — vision extract, LLM translate, or vision end-to-end. Classic OCR is optional.
3. **OBS-friendly** — Browser Source at `http://127.0.0.1:8765/` plus a frameless overlay window.
4. **Custom subtitle fonts** — shared `SubtitleStyle` for overlay and OBS (`@font-face` via `/fonts/custom`).

## Quick start

### Linux build dependencies (Debian/Ubuntu)

```bash
sudo apt install pkg-config libpipewire-0.3-dev libclang-dev \
  libegl1-mesa-dev libgl1-mesa-dev libgbm-dev \
  libxkbcommon-dev libxcb-render0-dev libxcb-shape0-dev \
  libxcb-xfixes0-dev libx11-dev libwayland-dev
```

### Build & run (Linux / macOS)

```bash
# Build
cargo build -p galmaster --release

# Optional: write default config
cargo run -p galmaster -- init-config

# Run GUI
cargo run -p galmaster -- run
# or
cargo run -p galmaster
```

### Windows

See **[docs/windows.md](docs/windows.md)** for Visual Studio / MSVC setup, build, run, OBS, and troubleshooting.

**Prebuilt (MSVC ABI):** `dist/windows/galmaster.exe` (~13 MB, `x86_64-pc-windows-msvc`).  
Do **not** use MinGW (`windows-gnu`) builds for distribution — Windows Defender often false-positives them.

```powershell
cd dist\windows
.\galmaster.exe init-config
$env:GALMASTER_API_KEY = "sk-..."
.\galmaster.exe
```

Rebuild from Linux: `./scripts/build_windows_msvc.sh` (see [docs/windows.md](docs/windows.md) if Defender still quarantines).

Quick path from source (PowerShell, Rust MSVC + “Desktop development with C++”):

```powershell
cd GalMaster
cargo build -p galmaster --release
$env:GALMASTER_API_KEY = "sk-..."
cargo run -p galmaster --release
```

Config default path: **`config.toml` next to the executable**  
(e.g. `dist\windows\config.toml` when you run `galmaster.exe` from that folder).  
Override with `--config path\to\config.toml`.

API keys: set `GALMASTER_API_KEY` or put `api_key` in the config stages.

## Pipeline

**Vision-language e2e only:** ROI image → multimodal model → `{original, translated}`.

Configure in the GUI under **Vision model (e2e)**:

- Provider: `openai_compat` or `anthropic`
- Model / Base URL / API key env / Target language

Compatible with any OpenAI- or Anthropic-compatible vision endpoint (including local gateways).

## OBS

1. Start GalMaster.
2. OBS → Sources → **Browser** → URL `http://127.0.0.1:8765/` (see `[obs].bind`).
3. Transparent page receives WebSocket subtitle + style updates.

## Workspace crates

| Crate | Role |
|-------|------|
| `galmaster-core` | Types, gates, config, style |
| `galmaster-capture` | Window list + ROI capture (`xcap`) |
| `galmaster-provider` | OpenAI / Anthropic HTTP |
| `galmaster-extract` | Vision / OCR extractors |
| `galmaster-understand` | Vision e2e |
| `galmaster-translate` | Text translators |
| `galmaster-obs` | HTTP + WS server |
| `galmaster-ui` | egui settings + overlay |
| `galmaster` | Binary |

## Tests

```bash
cargo test --workspace
cargo test -p galmaster --test smoke
```

### Plasma Wayland + Breeze (Linux desktop smoke)

Install (Ubuntu):

```bash
sudo apt install plasma-desktop plasma-session-wayland kwin-wayland \
  breeze breeze-cursor-theme xwayland fonts-noto-cjk
```

Run virtual KWin (no physical monitor required) + GalMaster UI:

```bash
cargo build -p galmaster
# optional full shell chrome: START_PLASMA_SHELL=1
WITH_UI=1 ./scripts/run_plasma_wayland_smoke.sh 15
```

Or headless functional path without GUI:

```bash
GALMASTER_SMOKE_EPHEMERAL=1 cargo run -p galmaster -- desktop-smoke --seconds 8
```

See also [docs/windows.md](docs/windows.md) for Windows usage.

## Future

- ASR profile (`asr_then_translate`) reusing the same `TranslationEvent` sinks
- In-process LiteRT-LM (`feature = "litert-lm"`)
- Real Tesseract / ONNX OCR backends
