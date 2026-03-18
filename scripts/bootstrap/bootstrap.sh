#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST_OS="$(uname -s)"
OPENCLAW_VERSION="${OPENCLAW_VERSION:-2026.3.13}"
ACPX_VERSION="${ACPX_VERSION:-0.3.0}"
BUN_INSTALL="${BUN_INSTALL:-$HOME/.bun}"
VARLOCK_BIN_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/varlock/bin"
LEGACY_VARLOCK_BIN_DIR="$HOME/.varlock/bin"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/docker_runtime.sh"

prepend_path() {
  local candidate="$1"
  if [[ -z "$candidate" || ! -d "$candidate" ]]; then
    return 0
  fi
  case ":$PATH:" in
    *":$candidate:"*) ;;
    *) PATH="$candidate:$PATH" ;;
  esac
}

ensure_varlock() {
  if command -v varlock >/dev/null 2>&1; then
    return 0
  fi

  curl -sSfL https://varlock.dev/install.sh | sh -s -- --force-no-brew
  prepend_path "$VARLOCK_BIN_DIR"
  prepend_path "$LEGACY_VARLOCK_BIN_DIR"

  command -v varlock >/dev/null 2>&1 || {
    echo "varlock install failed" >&2
    exit 1
  }
}

"$ROOT/scripts/bootstrap/preflight.sh"

case "$HOST_OS" in
  Darwin)
    command -v brew >/dev/null 2>&1 || {
      echo "Homebrew is required" >&2
      exit 1
    }
    brew install jq sqlite python
    brew install node
    brew install dmno-dev/tap/varlock
    brew install bun
    ensure_docker_compatible_runtime darwin
    python3 -m pip install -e "$ROOT"
    npm install -g "openclaw@${OPENCLAW_VERSION}" "acpx@${ACPX_VERSION}"
    ;;
  Linux)
    sudo apt-get update
    sudo apt-get install -y python3 python3-pip jq sqlite3 nodejs npm curl unzip
    if ! command -v bun >/dev/null 2>&1; then
      curl -fsSL https://bun.sh/install | bash
    fi
    export BUN_INSTALL
    export PATH="$BUN_INSTALL/bin:$PATH"
    ensure_varlock
    ensure_docker_compatible_runtime linux
    python3 -m pip install -e "$ROOT"
    sudo npm install -g "openclaw@${OPENCLAW_VERSION}" "acpx@${ACPX_VERSION}"
    ;;
  *)
    echo "unsupported host OS for bootstrap: $HOST_OS" >&2
    exit 1
    ;;
esac

"$ROOT/scripts/bootstrap/bootstrap_qmd.sh"
"$ROOT/scripts/bootstrap/bootstrap_memory_plugin.sh"

if [[ "$HOST_OS" == "Linux" && "$DOCKER_RUNTIME_INSTALLED_BY_BOOTSTRAP" -eq 1 ]]; then
  repair_linux_runtime_user_docker_access "$(id -un)"
fi

mkdir -p "$HOME/.openclaw/clawops" "$HOME/.openclaw/logs" "$ROOT/platform/compose/state"
command -v openclaw >/dev/null 2>&1 || { echo "openclaw install failed" >&2; exit 1; }
command -v acpx >/dev/null 2>&1 || { echo "acpx install failed" >&2; exit 1; }
"$ROOT/scripts/bootstrap/render_openclaw_config.sh"
"$ROOT/scripts/bootstrap/doctor_host.sh"
echo "Bootstrap complete."
