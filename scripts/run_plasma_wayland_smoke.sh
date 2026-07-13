#!/usr/bin/env bash
# Start KWin Wayland on a virtual framebuffer (Breeze-oriented env) and run GalMaster desktop-smoke.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [[ ! -d "$XDG_RUNTIME_DIR" ]]; then
  export XDG_RUNTIME_DIR="/tmp/xdg-runtime-$(id -u)"
  mkdir -p "$XDG_RUNTIME_DIR"
  chmod 700 "$XDG_RUNTIME_DIR"
fi

# Xwayland needs this directory
sudo mkdir -p /tmp/.X11-unix 2>/dev/null || mkdir -p /tmp/.X11-unix
sudo chmod 1777 /tmp/.X11-unix 2>/dev/null || chmod 1777 /tmp/.X11-unix || true

export XDG_SESSION_TYPE=wayland
export XDG_CURRENT_DESKTOP=KDE
export KDE_SESSION_VERSION=6
export KDE_FULL_SESSION=true
export QT_QPA_PLATFORM=wayland
export QT_WAYLAND_DISABLE_WINDOWDECORATION=0
# Prefer Breeze look
export XCURSOR_THEME=breeze_cursors
export XCURSOR_SIZE=24
export PLASMA_USE_QT_SCALING=1

# Avoid taking over a real seat when possible
export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"
export WINIT_UNIX_BACKEND=wayland
export GALMASTER_SMOKE_EPHEMERAL=1

SOCKET_NAME="galmaster-wl-$$"
SECONDS_RUN="${1:-15}"
WITH_UI="${WITH_UI:-1}"
# plasmashell is heavy and needs kactivitymanagerd; default off for CI-like hosts
START_PLASMA_SHELL="${START_PLASMA_SHELL:-0}"

BIN="${ROOT}/target/debug/galmaster"
if [[ ! -x "$BIN" ]]; then
  cargo build -p galmaster
fi

SESSION_SCRIPT="$(mktemp)"
RESULT_FILE="$(mktemp)"
cleanup() {
  rm -f "$SESSION_SCRIPT" "$RESULT_FILE"
}
trap cleanup EXIT

cat >"$SESSION_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
# kwin already exports WAYLAND_DISPLAY for --exit-with-session; keep fallback
export WAYLAND_DISPLAY="\${WAYLAND_DISPLAY:-${SOCKET_NAME}}"
export XDG_SESSION_TYPE=wayland
export XDG_CURRENT_DESKTOP=KDE
export KDE_SESSION_VERSION=6
export QT_QPA_PLATFORM=wayland
export WINIT_UNIX_BACKEND=wayland
export XCURSOR_THEME=breeze_cursors
export GALMASTER_SMOKE_EPHEMERAL=1
export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"

echo "session: WAYLAND_DISPLAY=\$WAYLAND_DISPLAY XDG_RUNTIME_DIR=\$XDG_RUNTIME_DIR"
ls -la "\$XDG_RUNTIME_DIR" | head -20 || true

# Wait until wayland socket appears
for i in \$(seq 1 50); do
  if [[ -S "\$XDG_RUNTIME_DIR/\$WAYLAND_DISPLAY" ]] || [[ -S "\$XDG_RUNTIME_DIR/\$WAYLAND_DISPLAY.lock" ]]; then
    break
  fi
  # also accept wayland-0 style
  if compgen -G "\$XDG_RUNTIME_DIR/wayland-*" >/dev/null; then
    break
  fi
  sleep 0.1
done

# Apply Breeze (best-effort)
if command -v plasma-apply-lookandfeel >/dev/null 2>&1; then
  plasma-apply-lookandfeel -a org.kde.breeze.desktop >/tmp/galmaster-breeze.log 2>&1 || true
fi
if command -v plasma-apply-desktoptheme >/dev/null 2>&1; then
  plasma-apply-desktoptheme breeze >/tmp/galmaster-desktoptheme.log 2>&1 || true
fi

if [[ "${START_PLASMA_SHELL}" == "1" ]] && command -v plasmashell >/dev/null 2>&1; then
  # full shell needs activity manager — start if available
  if command -v kactivitymanagerd >/dev/null 2>&1; then
    kactivitymanagerd >/tmp/galmaster-kactivity.log 2>&1 &
    sleep 1
  fi
  plasmashell --no-respawn >/tmp/galmaster-plasmashell.log 2>&1 &
  sleep 2
fi

ARGS=(desktop-smoke --seconds ${SECONDS_RUN})
if [[ "${WITH_UI}" == "1" ]]; then
  ARGS+=(--with-ui)
fi

set +e
"${BIN}" "\${ARGS[@]}"
echo \$? > "${RESULT_FILE}"
set -e

sleep 1
pkill -f 'plasmashell --no-respawn' 2>/dev/null || true
pkill -f kactivitymanagerd 2>/dev/null || true
EOF
chmod +x "$SESSION_SCRIPT"

echo "==> Starting kwin_wayland --virtual (socket=${SOCKET_NAME}, ${SECONDS_RUN}s, with_ui=${WITH_UI}, shell=${START_PLASMA_SHELL})"
# --virtual: software virtual framebuffer, no physical display required
# --xwayland: help toolkits that still want X
set +e
dbus-run-session -- kwin_wayland \
  --virtual \
  --width 1920 \
  --height 1080 \
  --xwayland \
  --socket "$SOCKET_NAME" \
  --exit-with-session "$SESSION_SCRIPT"
KWIN_RC=$?
set -e

RC=1
if [[ -f "$RESULT_FILE" ]]; then
  RC="$(cat "$RESULT_FILE")"
fi

echo "==> kwin exit=${KWIN_RC} galmaster smoke exit=${RC}"
if [[ -f /tmp/galmaster-breeze.log ]]; then
  echo "==> breeze apply log:"; tail -20 /tmp/galmaster-breeze.log || true
fi
if [[ -f /tmp/galmaster-plasmashell.log ]]; then
  echo "==> plasmashell log (tail):"; tail -30 /tmp/galmaster-plasmashell.log || true
fi

exit "$RC"
