# GalMaster

Real-time **window/ROI capture → vision-language e2e translate → overlay / OBS**, written in Rust.

<img width="3840" height="2160" alt="圖片" src="https://github.com/user-attachments/assets/58537fdc-1b10-4a8a-b849-0ee9e5594209" />

## Principles

1. **Human control stops at ROI** — pick a window and box the subtitle band; the rest is automated.
2. **Live path is vision e2e** — one multimodal call on the ROI image returns `{original, translated}`. Separate extract→translate / classic OCR crates exist for library reuse and future profiles, but the running app uses **vision e2e only**.
3. **OBS-friendly** — Browser Source at `http://127.0.0.1:8765/` plus an optional frameless overlay window.
4. **Shared subtitle style** — one `SubtitleStyle` drives overlay and OBS (`@font-face` via `/fonts/custom`).

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
`scripts/build_windows_msvc.sh` is a thin wrapper around `build.sh windows`.

Local packaging also supports macOS / linux-arm64 / windows-gnu when the host can build them; **GitHub Releases only ship Linux + Windows x86_64** (see below).

### GitHub Actions (auto build + Release)

Workflow: [`.github/workflows/release.yml`](.github/workflows/release.yml)

| Event | Result |
|-------|--------|
| **Push to `main` / `master`** | Build Linux / Windows → **new** prerelease `continuous-<sha>` |
| **Push tag `v*`** (e.g. `v0.1.0`) | Same builds → versioned stable **GitHub Release** |
| **Pull request** | Build only (no release) |
| **workflow_dispatch** | Same as branch push → new prerelease |

Shipped assets: `linux-x86_64`, `windows-x86_64` (MSVC).

```bash
# Publish a stable release after merging to main
git tag v0.1.0
git push origin v0.1.0
```

Download from the repo **Releases** page: newest `continuous-<sha>` prerelease for tip-of-main, or a `v*` release for stable.

### Build & run (from source)

```bash
# Or: ./scripts/build.sh && ./dist/linux-x86_64/galmaster
cargo build -p galmaster --release

# Write default config next to the binary (portable layout)
cargo run -p galmaster -- init-config

# Print default TOML to stdout
cargo run -p galmaster -- print-config

# Run GUI (default if no subcommand)
cargo run -p galmaster -- run
# or
cargo run -p galmaster
```

Override config path with `--config path/to/config.toml` (global flag).

### Windows

See **[docs/windows.md](docs/windows.md)** for Visual Studio / MSVC setup, build, run, OBS, and Defender notes.

**Prebuilt (MSVC ABI):** `dist/windows-x86_64/galmaster.exe` (`x86_64-pc-windows-msvc`).  
Do **not** ship MinGW (`windows-gnu`) builds — Windows Defender often false-positives them.

```powershell
cd dist\windows-x86_64
.\galmaster.exe init-config
# set api_key under [pipeline.vision] in config.toml, or in the GUI
.\galmaster.exe
```

Rebuild from Linux/macOS: `./scripts/build.sh windows` (see [docs/windows.md](docs/windows.md) if Defender still quarantines).

From source (PowerShell, Rust MSVC + “Desktop development with C++”):

```powershell
cd GalMaster
cargo build -p galmaster --release
cargo run -p galmaster --release
```

### Config & API key

Default config path: **`config.toml` next to the running executable**  
(e.g. `dist/linux-x86_64/config.toml` or `dist\windows-x86_64\config.toml`).

- Copy from [`config.example.toml`](config.example.toml) or run `init-config`.
- API key: set `api_key` under **`[pipeline.vision]`** in TOML, or in the GUI (**Vision model** section).  
  There is no separate env-var field; omit the key only if your local gateway needs no auth.
- Optional sampling (`temperature`, `max_tokens`, …) and structured-output options live under `[pipeline.vision]` / `[pipeline.vision.structured]` — unset fields are **not sent** (server defaults apply).

## Pipeline (live)

```text
Capture ROI → FrameGate (pixel stillness) → VLM (vision e2e) → ResultGate → Overlay / OBS
```

Configure in the GUI under **Vision model (e2e)** or in TOML:

| Setting | Where | Notes |
|---------|--------|--------|
| Provider | `openai_compat` / `anthropic` | Any compatible vision endpoint (incl. local gateways) |
| Model / base URL / API key | `[pipeline.vision]` | Single `api_key` field |
| Target language | `[pipeline.translate].target_lang` | Injected into the e2e prompt |
| Context lines | `[pipeline.translate]` | Previous lines into the prompt (`context_enabled`, `max_context_lines`, `context_mode`) |
| Capture / ROI / scale | `[capture]` | Window match, ROI, `target_fps`, `image_scale`, filter |
| Gates | `[gate]` | See below |

### Gates

| Knob | Role |
|------|------|
| `pixel_diff_threshold` | Mean luma sample diff: below = “same frame” |
| `stable_frames` | **ROI stillness only** — need N consecutive similar frames before calling the VLM (debounce typing/fades). `1` = fire on first novel frame |
| `text_similarity_skip` | After the VLM: skip publish if original+translated is too similar to the last accepted result |

While the ROI is still animating, the stillness counter resets; status shows *Waiting — frame stabilizing* until the picture holds still.

## OBS

1. Start GalMaster.
2. OBS → Sources → **Browser** → URL `http://127.0.0.1:8765/` (see `[obs].bind`).
3. Transparent page receives WebSocket subtitle + style updates.

Optional floating overlay: `[overlay]` (chroma key default for capture-friendly compositing).

## Workspace crates

| Crate | Role |
|-------|------|
| `galmaster-core` | Types, gates, config, style, pipeline handle |
| `galmaster-capture` | Window list + ROI capture (`xcap`) |
| `galmaster-provider` | OpenAI-compatible / Anthropic HTTP client |
| `galmaster-understand` | Vision e2e (live path) |
| `galmaster-extract` | Vision / OCR extractors (**library**; not wired into the live worker) |
| `galmaster-translate` | Text translators (**library**; not wired into the live worker) |
| `galmaster-obs` | HTTP + WS overlay server |
| `galmaster-ui` | egui settings + floating overlay |
| `galmaster` | Binary: capture loop, worker, GUI, CLI |

## CLI

| Command | Purpose |
|---------|---------|
| `run` (default) | GUI + capture worker + OBS server |
| `init-config` | Write default `config.toml` next to the binary |
| `print-config` | Print default TOML to stdout |
| `desktop-smoke` | Headless/desktop smoke: OBS + mock subtitles (no API) |

```bash
galmaster desktop-smoke --seconds 8
galmaster desktop-smoke --with-ui --seconds 12
# ephemeral config under /tmp when set:
GALMASTER_SMOKE_EPHEMERAL=1 galmaster desktop-smoke --seconds 8
```

## Tests

```bash
cargo test --workspace
cargo test -p galmaster --test smoke
cargo clippy --workspace --all-targets -- -D warnings
```

### Plasma Wayland + Breeze (Linux desktop smoke)

Install (Ubuntu):

```bash
sudo apt install plasma-desktop plasma-session-wayland kwin-wayland \
  breeze breeze-cursor-theme xwayland fonts-noto-cjk
```

```bash
cargo build -p galmaster
# optional full shell chrome: START_PLASMA_SHELL=1
WITH_UI=1 ./scripts/run_plasma_wayland_smoke.sh 15
```

Headless functional path without GUI:

```bash
GALMASTER_SMOKE_EPHEMERAL=1 cargo run -p galmaster -- desktop-smoke --seconds 8
```

Details: [docs/linux-plasma-smoke.md](docs/linux-plasma-smoke.md). Windows usage: [docs/windows.md](docs/windows.md).

## Future

- Optional `extract_then_translate` / classic OCR profile reusing the same `TranslationEvent` sinks
- ASR profile (`asr_then_translate`)
- Local OpenAI-compatible servers (LiteRT-LM, Ollama, vLLM, …) already work via `openai_compat` + `base_url`
