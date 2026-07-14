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

### Multi-platform release build

Use **`./scripts/build.sh`** to produce packaged binaries under `dist/`:

```bash
./scripts/build.sh --list          # what this host can build
./scripts/build.sh                 # host only → dist/linux-x86_64/ (etc.)
./scripts/build.sh windows         # Windows MSVC via cargo-xwin (from Linux/macOS)
./scripts/build.sh linux windows   # several targets
./scripts/build.sh all             # every feasible target on this machine
```

Each package folder contains the binary, `.sha256`, `README.txt`, and `config.example.toml`.  
`scripts/build_windows_msvc.sh` remains as a thin wrapper around `build.sh windows`.

### GitHub Actions (auto build + Release)

Workflow: [`.github/workflows/release.yml`](.github/workflows/release.yml)

| Event | Result |
|-------|--------|
| **Push to `main` / `master`** | Build Linux / Windows → update prerelease **`continuous`** |
| **Push tag `v*`** (e.g. `v0.1.0`) | Same builds → versioned **GitHub Release** |
| **Pull request** | Build only (no release) |
| **workflow_dispatch** | Same as push to default branch |

Platforms: `linux-x86_64`, `windows-x86_64` (MSVC).

```bash
# Publish a stable release after merging to main
git tag v0.1.0
git push origin v0.1.0
```

Download assets from the repo **Releases** page (or the `continuous` prerelease for the latest main build).

### Build & run (Linux / macOS)

```bash
# Or: ./scripts/build.sh && ./dist/linux-x86_64/galmaster
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

**Prebuilt (MSVC ABI):** `dist/windows/galmaster.exe` (`x86_64-pc-windows-msvc`).  
Do **not** use MinGW (`windows-gnu`) builds for distribution — Windows Defender often false-positives them.

```powershell
cd dist\windows
.\galmaster.exe init-config
# set api_key under [pipeline.vision] in config.toml, or in the GUI
.\galmaster.exe
```

Rebuild from Linux/macOS: `./scripts/build.sh windows` (see [docs/windows.md](docs/windows.md) if Defender still quarantines).

Quick path from source (PowerShell, Rust MSVC + “Desktop development with C++”):

```powershell
cd GalMaster
cargo build -p galmaster --release
cargo run -p galmaster --release
```

Config default path: **`config.toml` next to the executable**  
(e.g. `dist\windows\config.toml` when you run `galmaster.exe` from that folder).  
Override with `--config path\to\config.toml`.

API key: set `api_key` under `[pipeline.vision]` in `config.toml`, or in the GUI (Vision model section).

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
