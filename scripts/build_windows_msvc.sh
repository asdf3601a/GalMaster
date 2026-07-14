#!/usr/bin/env bash
# Back-compat wrapper → scripts/build.sh windows
# Cross-build GalMaster for Windows MSVC (preferred over MinGW for Defender).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/build.sh" windows "$@"
