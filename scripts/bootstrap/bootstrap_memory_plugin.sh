#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PLUGIN_DIR="$ROOT/platform/plugins/memory-lancedb-pro"

compat_field() {
  PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" -m clawops.platform_compat --field "$1"
}

HOST_OS="$(compat_field host_os)"
HOST_ARCH="$(compat_field host_arch)"
DEFAULT_LANCEDB_VERSION="$(compat_field memory_plugin_default_lancedb_version)"
RESOLVED_LANCEDB_VERSION="$(compat_field memory_plugin_lancedb_version)"

command -v npm >/dev/null 2>&1 || { echo "npm is required" >&2; exit 1; }
cd "$PLUGIN_DIR"

npm ci
if [[ "$RESOLVED_LANCEDB_VERSION" != "$DEFAULT_LANCEDB_VERSION" ]]; then
  npm install --no-fund --no-audit --no-save "@lancedb/lancedb@$RESOLVED_LANCEDB_VERSION"
  echo "Installed host-compatible LanceDB $RESOLVED_LANCEDB_VERSION for $HOST_OS/$HOST_ARCH."
else
  echo "Installed default LanceDB $RESOLVED_LANCEDB_VERSION for $HOST_OS/$HOST_ARCH."
fi
