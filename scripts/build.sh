#!/usr/bin/env bash
# GalMaster multi-platform release build → dist/<platform>/
#
# Usage:
#   ./scripts/build.sh                  # host platform only
#   ./scripts/build.sh host
#   ./scripts/build.sh windows          # x86_64 Windows MSVC (xwin on Linux/macOS)
#   ./scripts/build.sh linux windows
#   ./scripts/build.sh all              # every target feasible on this host
#   ./scripts/build.sh --list
#   ./scripts/build.sh --help
#
# Options:
#   --debug           Debug profile (default: release)
#   --no-package      Build only; skip copy into dist/
#   --skip-deps       Do not rustup target add / cargo-xwin install
#   -j, --jobs N      Parallel cargo jobs (passed to cargo)
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROFILE="release"
PACKAGE=1
SKIP_DEPS=0
CARGO_JOBS=()
REQUESTED=()

# ── colours (if tty) ──────────────────────────────────────────────
if [[ -t 1 ]]; then
  C_INFO=$'\033[1;34m'
  C_OK=$'\033[1;32m'
  C_WARN=$'\033[1;33m'
  C_ERR=$'\033[1;31m'
  C_DIM=$'\033[2m'
  C_RST=$'\033[0m'
else
  C_INFO= C_OK= C_WARN= C_ERR= C_DIM= C_RST=
fi

log()  { printf '%s==>%s %s\n' "$C_INFO" "$C_RST" "$*"; }
ok()   { printf '%s✓%s %s\n' "$C_OK" "$C_RST" "$*"; }
warn() { printf '%s!%s %s\n' "$C_WARN" "$C_RST" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$C_ERR" "$C_RST" "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
GalMaster multi-platform build

Usage:
  ./scripts/build.sh [options] [targets...]

Targets (aliases):
  host                 Native host triple (default if none given)
  linux | linux-x64    x86_64-unknown-linux-gnu
  linux-arm64          aarch64-unknown-linux-gnu
  windows | win | windows-x64
                       x86_64-pc-windows-msvc  (MSVC ABI; preferred)
  windows-gnu          x86_64-pc-windows-gnu   (dev only; Defender risk)
  macos | macos-arm64  aarch64-apple-darwin
  macos-x64            x86_64-apple-darwin
  all                  Every target that can be built on this host

Options:
  --debug              cargo build (debug), not --release
  --no-package         Skip packaging into dist/
  --skip-deps          Skip rustup target add / tool install
  -j, --jobs N         cargo -j N
  --list               List targets and host feasibility
  -h, --help           This help

Output layout (release):
  dist/<name>/galmaster[.exe]
  dist/<name>/galmaster[.exe].sha256
  dist/<name>/README.txt
  dist/<name>/config.example.toml

Examples:
  ./scripts/build.sh
  ./scripts/build.sh windows
  ./scripts/build.sh linux windows
  ./scripts/build.sh all
EOF
}

# ── host detection ────────────────────────────────────────────────
HOST_TRIPLE="$(rustc -vV 2>/dev/null | sed -n 's/^host: //p' || true)"
[[ -n "$HOST_TRIPLE" ]] || die "rustc not found; install Rust first"

HOST_OS=unknown
case "$HOST_TRIPLE" in
  *-linux-*)   HOST_OS=linux ;;
  *-apple-*)   HOST_OS=macos ;;
  *-windows-*) HOST_OS=windows ;;
esac

VERSION="$(
  sed -n 's/^version = "\([^"]*\)"/\1/p' Cargo.toml | head -1
)"
[[ -n "$VERSION" ]] || VERSION="0.0.0"

# name|rust_triple|exe_name|dist_subdir|notes
# shellcheck disable=SC2034
TARGET_TABLE=$(cat <<'EOF'
linux|x86_64-unknown-linux-gnu|galmaster|linux-x86_64|Linux x86_64 (glibc)
linux-x64|x86_64-unknown-linux-gnu|galmaster|linux-x86_64|alias
linux-arm64|aarch64-unknown-linux-gnu|galmaster|linux-aarch64|Linux aarch64 (glibc)
windows|x86_64-pc-windows-msvc|galmaster.exe|windows|Windows x86_64 MSVC (recommended)
win|x86_64-pc-windows-msvc|galmaster.exe|windows|alias
windows-x64|x86_64-pc-windows-msvc|galmaster.exe|windows|alias
windows-gnu|x86_64-pc-windows-gnu|galmaster.exe|windows-gnu|Windows MinGW — do not ship
macos|aarch64-apple-darwin|galmaster|macos-aarch64|macOS Apple Silicon
macos-arm64|aarch64-apple-darwin|galmaster|macos-aarch64|alias
macos-x64|x86_64-apple-darwin|galmaster|macos-x86_64|macOS Intel
host|||host|Native host triple
EOF
)

resolve_target() {
  # $1 = alias → prints: triple exe dist notes  (or dies)
  local alias="$1"
  if [[ "$alias" == "host" ]]; then
    local exe=galmaster
    case "$HOST_TRIPLE" in
      *-windows-*) exe=galmaster.exe ;;
    esac
    local dist
    case "$HOST_TRIPLE" in
      x86_64-unknown-linux-gnu)  dist=linux-x86_64 ;;
      aarch64-unknown-linux-gnu) dist=linux-aarch64 ;;
      x86_64-pc-windows-msvc)    dist=windows ;;
      x86_64-pc-windows-gnu)     dist=windows-gnu ;;
      aarch64-apple-darwin)      dist=macos-aarch64 ;;
      x86_64-apple-darwin)       dist=macos-x86_64 ;;
      *)                         dist="host-${HOST_TRIPLE}" ;;
    esac
    printf '%s\n' "$HOST_TRIPLE" "$exe" "$dist" "host ($HOST_TRIPLE)"
    return
  fi
  local line name triple exe dist notes
  while IFS='|' read -r name triple exe dist notes; do
    [[ "$name" == "$alias" ]] || continue
    [[ -n "$triple" ]] || continue
    printf '%s\n' "$triple" "$exe" "$dist" "$notes"
    return
  done <<<"$TARGET_TABLE"
  die "unknown target: $alias (use --list)"
}

can_build_triple() {
  local triple="$1"
  if [[ "$triple" == "$HOST_TRIPLE" ]]; then
    return 0
  fi
  case "$triple" in
    x86_64-pc-windows-msvc)
      # cargo-xwin works from Linux/macOS; native MSVC on Windows
      if [[ "$HOST_OS" == "windows" ]]; then return 0; fi
      if [[ "$HOST_OS" == "linux" || "$HOST_OS" == "macos" ]]; then return 0; fi
      return 1
      ;;
    x86_64-pc-windows-gnu)
      # MinGW cross: require a mingw linker on the PATH
      if [[ "$HOST_OS" == "windows" ]]; then return 0; fi
      command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1 && return 0
      return 1
      ;;
    *-apple-darwin)
      # Apple cross from non-mac is impractical without osxcross
      [[ "$HOST_OS" == "macos" ]]
      ;;
    *-linux-*)
      # Cross-linux needs linker; only allow same-os host or when cross linker exists
      if [[ "$HOST_OS" == "linux" ]]; then
        # native arch always ok; other arch needs aarch64/x86_64 cross gcc
        if [[ "$triple" == "$HOST_TRIPLE" ]]; then return 0; fi
        case "$triple" in
          aarch64-unknown-linux-gnu)
            command -v aarch64-linux-gnu-gcc >/dev/null 2>&1 && return 0
            return 1
            ;;
          x86_64-unknown-linux-gnu)
            command -v x86_64-linux-gnu-gcc >/dev/null 2>&1 && return 0
            # on aarch64 host without cross gcc
            return 1
            ;;
        esac
      fi
      return 1
      ;;
    *)
      return 1
      ;;
  esac
}

list_targets() {
  echo "Host: $HOST_TRIPLE  (os=$HOST_OS)  version=$VERSION"
  echo
  printf '%-14s %-32s %-8s %s\n' "NAME" "TRIPLE" "OK?" "NOTES"
  printf '%-14s %-32s %-8s %s\n' "----" "------" "---" "-----"
  local seen_triples=""
  local name triple exe dist notes okflag
  while IFS='|' read -r name triple exe dist notes; do
    if [[ "$name" == "host" ]]; then
      printf '%-14s %-32s %-8s %s\n' "host" "$HOST_TRIPLE" "yes" "native"
      continue
    fi
    # skip pure aliases in list (show canonical names only)
    case "$name" in
      linux-x64|win|windows-x64|macos-arm64) continue ;;
    esac
    if [[ " $seen_triples " == *" $triple "* ]]; then
      continue
    fi
    seen_triples+=" $triple"
    if can_build_triple "$triple"; then okflag=yes; else okflag=no; fi
    printf '%-14s %-32s %-8s %s\n' "$name" "$triple" "$okflag" "$notes"
  done <<<"$TARGET_TABLE"
  echo
  echo "Feasible with: ./scripts/build.sh all"
}

ensure_rust_target() {
  local triple="$1"
  [[ "$SKIP_DEPS" -eq 1 ]] && return 0
  [[ "$triple" == "$HOST_TRIPLE" ]] && return 0
  if ! rustup target list --installed 2>/dev/null | grep -qx "$triple"; then
    log "rustup target add $triple"
    rustup target add "$triple"
  fi
}

ensure_cargo_xwin() {
  [[ "$SKIP_DEPS" -eq 1 ]] && return 0
  if ! command -v cargo-xwin >/dev/null 2>&1; then
    log "Installing cargo-xwin (first run may take a while)…"
    cargo install cargo-xwin --locked
  fi
}

cargo_target_dir() {
  # We always pass --target, so artifacts live under target/<triple>/{debug,release}/.
  local triple="$1"
  echo "$ROOT/target/$triple/$PROFILE"
}

build_one() {
  local alias="$1"
  local triple exe dist notes
  {
    read -r triple
    read -r exe
    read -r dist
    read -r notes
  } < <(resolve_target "$alias")

  if ! can_build_triple "$triple"; then
    warn "skip $alias ($triple): not feasible on host $HOST_TRIPLE"
    return 0
  fi

  log "Building $alias → $triple ($notes) [$PROFILE]"

  ensure_rust_target "$triple"

  local -a cmd=(cargo)
  local use_xwin=0
  local -a env_prefix=()

  if [[ "$triple" == "x86_64-pc-windows-msvc" && "$HOST_OS" != "windows" ]]; then
    ensure_cargo_xwin
    use_xwin=1
    # Static CRT: no VCRUNTIME140.dll; more typical Windows app footprint
    export RUSTFLAGS="${RUSTFLAGS:-} -C target-feature=+crt-static"
  fi

  if [[ "$use_xwin" -eq 1 ]]; then
    cmd=(cargo xwin build)
  else
    cmd=(cargo build)
  fi

  cmd+=(-p galmaster --target "$triple")
  if [[ "$PROFILE" == "release" ]]; then
    cmd+=(--release)
  fi
  if [[ ${#CARGO_JOBS[@]} -gt 0 ]]; then
    cmd+=("${CARGO_JOBS[@]}")
  fi

  log "${cmd[*]}"
  "${cmd[@]}"

  local out_dir
  out_dir="$(cargo_target_dir "$triple")"
  local built="$out_dir/$exe"
  [[ -f "$built" ]] || die "missing binary: $built"

  if [[ "$PACKAGE" -eq 0 ]]; then
    ok "built $built (packaging skipped)"
    return 0
  fi

  local dest="$ROOT/dist/$dist"
  mkdir -p "$dest"
  cp -f "$built" "$dest/$exe"
  # optional strip for ELF (never strip Windows from Linux without llvm-strip awareness)
  if [[ "$PROFILE" == "release" && "$exe" == "galmaster" ]]; then
    if command -v strip >/dev/null 2>&1 && file "$dest/$exe" | grep -q 'ELF'; then
      strip "$dest/$exe" 2>/dev/null || true
    fi
  fi

  if command -v sha256sum >/dev/null 2>&1; then
    (cd "$dest" && sha256sum "$exe" | tee "${exe}.sha256")
  elif command -v shasum >/dev/null 2>&1; then
    (cd "$dest" && shasum -a 256 "$exe" | tee "${exe}.sha256")
  fi

  if [[ -f "$ROOT/config.example.toml" ]]; then
    cp -f "$ROOT/config.example.toml" "$dest/config.example.toml"
  fi

  write_readme "$dest" "$triple" "$exe" "$notes"
  if command -v file >/dev/null 2>&1; then
    file "$dest/$exe" || true
  fi
  ls -lh "$dest/$exe"
  ok "packaged dist/$dist/$exe"
}

write_readme() {
  local dest="$1" triple="$2" exe="$3" notes="$4"
  local run_cmd
  if [[ "$exe" == *.exe ]]; then
    run_cmd=".\\$exe"
  else
    run_cmd="./$exe"
  fi
  cat >"$dest/README.txt" <<EOF
GalMaster v${VERSION}
Target: ${triple}
${notes}

Build profile: ${PROFILE}
Host that built this: ${HOST_TRIPLE}

Quick start:
  ${run_cmd} init-config
  # set api_key in config.toml (or GUI)
  ${run_cmd}

Config: copy config.example.toml → config.toml (next to the binary), or run init-config.

OBS Browser Source: http://127.0.0.1:8765/

Windows notes (MSVC builds):
  Prefer x86_64-pc-windows-msvc over MinGW (windows-gnu). Unsigned EXEs may still
  be scanned by Defender; MSVC is less often a false positive. For distribution,
  Authenticode-sign the EXE. See docs/windows.md.
EOF
}

expand_all() {
  # Print feasible target *canonical names* for this host
  local name triple exe dist notes
  while IFS='|' read -r name triple exe dist notes; do
    case "$name" in
      host|linux-x64|win|windows-x64|macos-arm64) continue ;;
    esac
    [[ -n "$triple" ]] || continue
    if can_build_triple "$triple"; then
      echo "$name"
    fi
  done <<<"$TARGET_TABLE"
}

# ── parse args ────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --list) list_targets; exit 0 ;;
    --debug) PROFILE=debug; shift ;;
    --no-package) PACKAGE=0; shift ;;
    --skip-deps) SKIP_DEPS=1; shift ;;
    -j|--jobs)
      [[ $# -ge 2 ]] || die "$1 needs a value"
      CARGO_JOBS=(-j "$2")
      shift 2
      ;;
    --) shift; break ;;
    -*)
      die "unknown option: $1 (try --help)"
      ;;
    all)
      mapfile -t REQUESTED < <(expand_all)
      shift
      ;;
    *)
      REQUESTED+=("$1")
      shift
      ;;
  esac
done

if [[ ${#REQUESTED[@]} -eq 0 ]]; then
  REQUESTED=(host)
fi

# Deduplicate while preserving order
declare -A SEEN=()
UNIQUE=()
for t in "${REQUESTED[@]}"; do
  # normalize aliases to a stable build key (triple) for dedup packaging path
  key="$t"
  if [[ -n "${SEEN[$key]+x}" ]]; then
    continue
  fi
  SEEN[$key]=1
  UNIQUE+=("$t")
done

log "GalMaster v$VERSION  host=$HOST_TRIPLE  profile=$PROFILE"
log "Targets: ${UNIQUE[*]}"

FAILED=0
for t in "${UNIQUE[@]}"; do
  if ! build_one "$t"; then
    FAILED=1
  fi
done

if [[ "$FAILED" -ne 0 ]]; then
  die "one or more builds failed"
fi

if [[ "$PACKAGE" -eq 1 ]]; then
  echo
  log "Artifacts under dist/"
  find dist -maxdepth 2 -type f \( -name 'galmaster' -o -name 'galmaster.exe' -o -name '*.sha256' \) 2>/dev/null \
    | sort \
    | while read -r f; do ls -lh "$f"; done || true
fi

ok "all done"
