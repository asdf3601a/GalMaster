#!/usr/bin/env bash
# Cross-build GalMaster for Windows MSVC (preferred over MinGW for Defender false positives).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

rustup target add x86_64-pc-windows-msvc
if ! command -v cargo-xwin >/dev/null 2>&1; then
  echo "Installing cargo-xwin..."
  cargo install cargo-xwin --locked
fi

echo "==> cargo xwin build --release (x86_64-pc-windows-msvc, static CRT)"
# First run downloads MSVC CRT / SDK via xwin (large, cached under ~/.cache/cargo-xwin)
# +crt-static: no VCRUNTIME140.dll dependency; more “normal app” like footprint
export RUSTFLAGS="${RUSTFLAGS:-} -C target-feature=+crt-static"
cargo xwin build -p galmaster --release --target x86_64-pc-windows-msvc

mkdir -p dist/windows
cp -f target/x86_64-pc-windows-msvc/release/galmaster.exe dist/windows/galmaster.exe
file dist/windows/galmaster.exe || true
ls -lh dist/windows/galmaster.exe
sha256sum dist/windows/galmaster.exe | tee dist/windows/galmaster.exe.sha256

cat > dist/windows/README.txt << 'EOF'
GalMaster for Windows (x86_64-pc-windows-msvc)

This build uses the MSVC ABI (not MinGW). Unsigned EXEs can still be scanned by
Windows Defender; MSVC builds are much less often false-positive than MinGW.

If Defender still quarantines:
  1. Windows Security → Virus & threat protection → Protection history → Restore
  2. Add folder exclusion for this directory (or your install path)
  3. Prefer building on Windows with Visual Studio (see docs/windows.md)
  4. For distribution: Authenticode code-sign the EXE (best fix)

Quick start:
  .\galmaster.exe init-config
  $env:GALMASTER_API_KEY = "sk-..."
  .\galmaster.exe

OBS Browser Source: http://127.0.0.1:8765/
EOF

echo "==> done: dist/windows/galmaster.exe"
