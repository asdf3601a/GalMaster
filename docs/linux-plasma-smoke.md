# Linux: Plasma Wayland + Breeze functional smoke

This host can run GalMaster under **KWin Wayland** with a **virtual framebuffer** (no physical monitor), using Breeze-oriented environment variables.

## Packages (Ubuntu 26.04)

```bash
sudo apt install plasma-desktop plasma-session-wayland plasma-workspace \
  kwin-wayland breeze breeze-icon-theme breeze-cursor-theme \
  xwayland fonts-noto-cjk \
  libegl1-mesa-dev libgbm-dev libpipewire-0.3-dev pkg-config
```

User should be in `video` / `render` groups for DRM capture on a real seat:

```bash
sudo usermod -aG video,render $USER
# re-login
```

## Commands

```bash
# Unit + integration tests
cargo test --workspace
cargo test -p galmaster --test smoke

# OBS + mock subtitles only
GALMASTER_SMOKE_EPHEMERAL=1 cargo run -p galmaster -- desktop-smoke --seconds 10

# Full: KWin --virtual + Breeze env + GalMaster UI (auto-exits)
cargo build -p galmaster
WITH_UI=1 ./scripts/run_plasma_wayland_smoke.sh 15

# Optional: also start plasmashell (needs kactivitymanagerd)
START_PLASMA_SHELL=1 WITH_UI=1 ./scripts/run_plasma_wayland_smoke.sh 20
```

## What the smoke verifies

| Check | Virtual KWin result (this machine) |
|-------|--------------------------------------|
| KWin Wayland socket | OK (`WAYLAND_DISPLAY=galmaster-wl-…`) |
| XWayland | OK (`DISPLAY=:0`) |
| OBS HTML `/` | OK |
| OBS `/api/style` | OK |
| Mock subtitle inject | OK |
| egui UI open + auto-exit | OK |
| list_windows | Returns 0 under empty virtual session (expected) |
| capture_frame | Fails without wlr screencopy / portal on virtual output (expected) |

Real window capture works better on a **logged-in Plasma seat** (local login / SDDM) with portal permissions granted.

## Interactive Plasma session (real display)

1. Install SDDM if desired: `sudo apt install sddm && sudo dpkg-reconfigure sddm`
2. Choose **Plasma (Wayland)** at login
3. Apply Breeze: System Settings → Appearance → Global Theme → Breeze
4. Run `cargo run -p galmaster --release`
5. Point OBS Browser Source at `http://127.0.0.1:8765/`
