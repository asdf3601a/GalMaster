# GalMaster on Windows

Primary target platform. Capture uses Windows Graphics Capture via [`xcap`](https://crates.io/crates/xcap); UI is egui/`eframe`.

## 1. Prerequisites

### Rust (MSVC)

Install from [https://rustup.rs](https://rustup.rs). Prefer the **MSVC** host:

```powershell
rustup default stable-x86_64-pc-windows-msvc
rustup update
```

### C++ build tools

Install **Visual Studio 2022** (or Build Tools) with workload:

- **Desktop development with C++**

`xcap` / native deps need the MSVC linker and Windows SDK.

### Optional (local models)

- [LiteRT-LM](https://github.com/google-ai-edge/LiteRT-LM) CLI with OpenAI-compatible server, **or**
- Any OpenAI-compatible endpoint (OpenRouter, vLLM, Ollama with vision, etc.)

### Optional (OBS)

- [OBS Studio](https://obsproject.com/) for Browser Source overlay

## 2. Get the binary or build

### Option A — prebuilt (from this repo’s `dist/`)

```text
dist\windows\galmaster.exe
```

**Preferred build:** `x86_64-pc-windows-msvc` (via `./scripts/build.sh windows` / cargo-xwin).  
Avoid distributing **MinGW `windows-gnu`** builds — Defender frequently false-positives them as malware.

#### Windows Defender still quarantines?

Unsigned EXEs (especially new/rare hashes) can still be blocked. Mitigations, best → worst:

1. **Use the MSVC build** (this dist folder), not MinGW.
2. **Restore + exclusion:** Windows Security → Virus & threat protection → Protection history → allow; add exclusion for the install folder.
3. **Build on Windows with MSVC** (Option B) — same toolchain as normal apps.
4. **Authenticode code signing** (EV/OV certificate) — real distribution fix; Defender almost always trusts signed apps.
5. Submit false positive: https://www.microsoft.com/wdsi/filesubmission

Screen-capture + network apps are also more likely to be heuristically scanned; a stable version resource + manifest is already embedded to look less like a packer stub.

### Multi-platform script (Linux / macOS / Windows)

```bash
./scripts/build.sh --list
./scripts/build.sh windows          # → dist/windows/galmaster.exe
# legacy alias:
./scripts/build_windows_msvc.sh
```

On Windows with MSVC installed, the same script builds natively (`cargo build --target x86_64-pc-windows-msvc`).  
From Linux/macOS it uses **cargo-xwin** + static CRT.

### Option B — build on Windows (MSVC, recommended for local dev)

```powershell
git clone <your-repo-url> GalMaster
cd GalMaster

cargo build -p galmaster --release
```

Binary:

```text
target\release\galmaster.exe
```

First build downloads crates and may take several minutes.

### Option C — cross-compile from Linux (**MSVC, recommended**)

```bash
# needs: clang, lld; cargo-xwin is installed by the script if missing
# first run downloads MSVC CRT/SDK (large, cached under ~/.cache/cargo-xwin)
sudo apt install clang lld
./scripts/build.sh windows
# → dist/windows/galmaster.exe
```

MinGW (`x86_64-pc-windows-gnu` / `./scripts/build.sh windows-gnu`) is supported for development only; **do not ship it** if Defender is an issue.

## 3. Configure

### Create config

```powershell
.\target\release\galmaster.exe init-config
```

Default file (portable): **same folder as `galmaster.exe`**

```text
.\config.toml
```

Copy `config.example.toml` → `config.toml` beside the EXE, or run `init-config`.  
Override anytime: `.\galmaster.exe --config D:\path\config.toml`

### API key

Set a single `api_key` under `[pipeline.vision]` (GUI password field, or `config.toml`).  
OpenAI uses `Authorization: Bearer`; Anthropic uses `x-api-key`. Do not commit secrets.

```toml
[pipeline.vision]
api_key = "sk-your-key"
```

### Typical Windows config snippets

**Single vision e2e call:**

```toml
[pipeline]
profile = "vision_e2e"

[pipeline.vision]
provider = "openai_compat"
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api_key = "sk-your-key"
```

**Subtitle font (CJK):**

```toml
[style]
font_family = "Microsoft YaHei"
# or a file:
# font_path = "C:\\Windows\\Fonts\\msyh.ttc"
font_size_px = 42
outline_px = 3
show_translated = true
show_original = false
```

## 4. Run

```powershell
.\target\release\galmaster.exe
# or
.\target\release\galmaster.exe run
```

### In the GUI

1. **Refresh windows** → pick the player/game window (or type a title substring).
2. Set **ROI** to the subtitle band (or **Preset: bottom 20%**). Optional: **image scale** + filter (`nearest` / `bilinear` / `bicubic` / `lanczos`).
3. Set Vision `base_url` / **model** (Refresh models uses `GET /models`) / target language.
4. Optionally set **custom font path** under Subtitle style.
5. **Overlay window → Show floating overlay** can be turned off if you only use OBS Browser Source.
6. Click **Apply**, then **Start**. The top **Status** line shows capture / recognizing / done / errors.
7. Use **Inject mock event** first to verify Overlay + OBS without calling any API.

### Capture tips on Windows

| Topic | Recommendation |
|-------|----------------|
| Source | Prefer a **specific window title**, not full desktop (avoids capturing GalMaster Overlay). |
| Games | Borderless windowed is more reliable than exclusive fullscreen for WGC. |
| Admin | Usually **not** required; some protected UWP/fullscreen apps may not be capturable. |
| Overlay | Title is `GalMaster Overlay`. Disable with `overlay.enabled = false` or the GUI checkbox if you only need OBS Browser Source. |

## 5. OBS Studio (recommended presentation path)

1. Start GalMaster (OBS server listens on `127.0.0.1:8765` by default).
2. OBS → **Sources** → **Browser**:
   - URL: `http://127.0.0.1:8765/`
   - Width/Height: e.g. 1920×200 (or full canvas; page is transparent).
3. Subtitles and style (font, colors, outline) update over WebSocket.
4. Custom font files are served at `http://127.0.0.1:8765/fonts/custom` when `style.font_path` is set.

**Scene layout suggestion:**

- Game / player: **Game Capture** or **Window Capture**
- Subtitles: **Browser Source** → GalMaster (do not put GalMaster Overlay into the game capture)

If `[obs].bind` is changed in config, restart GalMaster and update the Browser Source URL.

## 6. LiteRT-LM (optional local translate)

1. Install / run LiteRT-LM with its **OpenAI-compatible server** (see upstream docs).
2. Point GalMaster translate (or whole openai_compat backend) at e.g. `http://127.0.0.1:8080/v1`.
3. Set `backend = "litert_lm_http"` or `openai_compat` with that `base_url`.
4. Pure-text local models only do **translate**; for `vision_e2e` / vision extract you need a multimodal endpoint.

## 7. Permissions & Windows settings

- **Screen capture privacy**: Windows 10/11 may prompt or restrict capture for some apps; allow desktop apps to capture if prompted.
- **Firewall**: only needed if you bind OBS server to non-localhost (default `127.0.0.1` stays local).
- **Antivirus**: first `cargo build` may be slow while scanning; exclude `target\` if trusted.

## 8. Troubleshooting

| Symptom | What to try |
|---------|-------------|
| `link.exe` / MSVC not found | Install VS “Desktop development with C++”; open “x64 Native Tools” shell or ensure `vcvars` is on PATH. |
| Empty window list | Run as normal user; try refreshing; some elevated/fullscreen apps are invisible to WGC. |
| Capture black frame | Switch game to borderless; disable exclusive fullscreen; try another window. |
| API 401 | Check `api_key` under `[pipeline.vision]` and `base_url`. |
| OBS blank | Confirm GalMaster is running; open `http://127.0.0.1:8765/` in a browser; check bind address. |
| CJK tofu (□□) | Set `[style].font_path` to a TTF/OTF/TTC that contains CJK (e.g. `msyh.ttc`, Noto Sans CJK). |
| High latency / cost | Shrink ROI; raise `pixel_diff_threshold`; lower FPS; prefer extract-then-local-translate over per-frame vision e2e. |

## 9. CLI reference

```powershell
galmaster.exe --help
galmaster.exe print-config
galmaster.exe init-config
galmaster.exe --config D:\path\config.toml run
```

## 10. Verify without streaming

1. Start GalMaster → **Inject mock event** → Overlay shows text.
2. Open browser → `http://127.0.0.1:8765/` → same text after inject / Start.
3. Configure a cheap vision model → Start with a static video player window and bottom ROI → confirm original/translated update and latency HUD.

For automated checks (from a dev machine with Rust):

```powershell
cargo test -p galmaster --test smoke
cargo test --workspace
```
