#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST_OS="$(uname -s)"
OPENCLAW_VERSION="${OPENCLAW_VERSION:-2026.3.13}"
ACPX_VERSION="${ACPX_VERSION:-0.3.0}"
UV_VERSION="${UV_VERSION:-0.10.9}"
OPENCLAW_CONFIG_PROFILE="${OPENCLAW_CONFIG_PROFILE:-default}"
BUN_INSTALL="${BUN_INSTALL:-$HOME/.bun}"
UV_BIN_DIR="${HOME}/.local/bin"
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

ensure_brew_formula() {
  local formula_name="$1"
  brew install "$formula_name"
}

ensure_command_or_brew() {
  local command_name="$1"
  local formula_name="$2"
  if command -v "$command_name" >/dev/null 2>&1; then
    return 0
  fi
  ensure_brew_formula "$formula_name"
}

python_satisfies_minimum() {
  if ! command -v python3 >/dev/null 2>&1; then
    return 1
  fi
  python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'
}

ensure_python_runtime() {
  if python_satisfies_minimum; then
    return 0
  fi
  ensure_brew_formula python
  python_satisfies_minimum || {
    echo "python3 >= 3.12 is required" >&2
    exit 1
  }
}

node_satisfies_minimum() {
  if ! command -v node >/dev/null 2>&1; then
    return 1
  fi
  node -e '
const [major, minor] = process.versions.node.split(".").map(Number);
process.exit(major > 22 || (major === 22 && minor >= 16) ? 0 : 1);
'
}

ensure_node_runtime() {
  if node_satisfies_minimum; then
    return 0
  fi
  ensure_brew_formula node
  node_satisfies_minimum || {
    echo "node >= 22.16 is required" >&2
    exit 1
  }
}

ensure_varlock() {
  prepend_path "$VARLOCK_BIN_DIR"
  prepend_path "$LEGACY_VARLOCK_BIN_DIR"
  if command -v varlock >/dev/null 2>&1; then
    return 0
  fi

  curl -sSfL https://varlock.dev/install.sh | sh -s -- --force-no-brew

  command -v varlock >/dev/null 2>&1 || {
    echo "varlock install failed" >&2
    exit 1
  }
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi

  curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" |
    env UV_UNMANAGED_INSTALL="$UV_BIN_DIR" sh
  prepend_path "$UV_BIN_DIR"

  command -v uv >/dev/null 2>&1 || {
    echo "uv install failed" >&2
    exit 1
  }
}

profile_requires_lossless_claw() {
  case "$OPENCLAW_CONFIG_PROFILE" in
    lossless-hypermemory-tier1) return 0 ;;
    *) return 1 ;;
  esac
}

"$ROOT/scripts/bootstrap/preflight.sh"

case "$HOST_OS" in
  Darwin)
    command -v brew >/dev/null 2>&1 || {
      echo "Homebrew is required" >&2
      exit 1
    }
    ensure_command_or_brew jq jq
    ensure_command_or_brew sqlite3 sqlite
    ensure_python_runtime
    ensure_node_runtime
    ensure_varlock
    ensure_command_or_brew bun bun
    ensure_docker_compatible_runtime darwin
    ensure_uv
    uv sync --project "$ROOT" --locked --extra dev
    prepend_path "$ROOT/.venv/bin"
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
    ensure_uv
    uv sync --project "$ROOT" --locked --extra dev
    prepend_path "$ROOT/.venv/bin"
    sudo npm install -g "openclaw@${OPENCLAW_VERSION}" "acpx@${ACPX_VERSION}"
    ;;
  *)
    echo "unsupported host OS for bootstrap: $HOST_OS" >&2
    exit 1
    ;;
esac

"$ROOT/scripts/bootstrap/bootstrap_qmd.sh"
"$ROOT/scripts/bootstrap/bootstrap_memory_plugin.sh"
if profile_requires_lossless_claw; then
  "$ROOT/scripts/bootstrap/bootstrap_lossless_context_engine.sh"
fi

if [[ "$HOST_OS" == "Linux" && "$DOCKER_RUNTIME_INSTALLED_BY_BOOTSTRAP" -eq 1 ]]; then
  repair_linux_runtime_user_docker_access "$(id -un)"
fi

mkdir -p "$HOME/.openclaw/clawops" "$HOME/.openclaw/logs" "$ROOT/platform/compose/state"
command -v openclaw >/dev/null 2>&1 || { echo "openclaw install failed" >&2; exit 1; }
command -v acpx >/dev/null 2>&1 || { echo "acpx install failed" >&2; exit 1; }
"$ROOT/scripts/bootstrap/render_openclaw_config.sh"
"$ROOT/scripts/bootstrap/doctor_host.sh"
echo "Bootstrap complete."
