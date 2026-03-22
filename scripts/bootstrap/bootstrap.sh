#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST_OS="$(uname -s)"
OPENCLAW_VERSION="${OPENCLAW_VERSION:-2026.3.13}"
ACPX_VERSION="${ACPX_VERSION:-0.3.0}"
UV_VERSION="${UV_VERSION:-0.10.9}"
VARLOCK_VERSION="${VARLOCK_VERSION:-0.5.0}"
OPENCLAW_CONFIG_PROFILE="${OPENCLAW_CONFIG_PROFILE:-${STRONGCLAW_DEFAULT_PROFILE:-hypermemory}}"
UV_BIN_DIR="${HOME}/.local/bin"
BOOTSTRAP_QMD_SCRIPT="${BOOTSTRAP_QMD_SCRIPT:-$ROOT/scripts/bootstrap/bootstrap_qmd.sh}"
BOOTSTRAP_MEMORY_PLUGIN_SCRIPT="${BOOTSTRAP_MEMORY_PLUGIN_SCRIPT:-$ROOT/scripts/bootstrap/bootstrap_memory_plugin.sh}"
BOOTSTRAP_LOSSLESS_CONTEXT_ENGINE_SCRIPT="${BOOTSTRAP_LOSSLESS_CONTEXT_ENGINE_SCRIPT:-$ROOT/scripts/bootstrap/bootstrap_lossless_context_engine.sh}"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/docker_runtime.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/setup_state.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/bootstrap_profiles.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/app_paths.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/varlock.sh"

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

ensure_linux_node_runtime() {
  if node_satisfies_minimum; then
    return 0
  fi
  command -v sudo >/dev/null 2>&1 || {
    echo "sudo is required to install node >= 22.16 on Linux." >&2
    exit 1
  }
  command -v apt-get >/dev/null 2>&1 || {
    echo "apt-get is required to install node >= 22.16 on Linux." >&2
    exit 1
  }
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y nodejs
  node_satisfies_minimum || {
    echo "node >= 22.16 is required" >&2
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

install_profile_assets() {
  if profile_requires_qmd "$OPENCLAW_CONFIG_PROFILE"; then
    "$BOOTSTRAP_QMD_SCRIPT"
  fi
  if profile_requires_memory_pro_plugin "$OPENCLAW_CONFIG_PROFILE"; then
    "$BOOTSTRAP_MEMORY_PLUGIN_SCRIPT"
  fi
  if profile_requires_lossless_claw "$OPENCLAW_CONFIG_PROFILE"; then
    "$BOOTSTRAP_LOSSLESS_CONTEXT_ENGINE_SCRIPT"
  fi
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
    ensure_varlock_installed "$VARLOCK_VERSION"
    ensure_docker_compatible_runtime darwin
    ensure_uv
    uv sync --project "$ROOT" --locked --extra dev
    prepend_path "$ROOT/.venv/bin"
    npm install -g "openclaw@${OPENCLAW_VERSION}" "acpx@${ACPX_VERSION}"
    ;;
  Linux)
    sudo apt-get update
    sudo apt-get install -y python3 python3-pip jq sqlite3 curl unzip ca-certificates gnupg
    ensure_linux_node_runtime
    ensure_varlock_installed "$VARLOCK_VERSION"
    ensure_docker_compatible_runtime linux
    ensure_uv
    uv sync --project "$ROOT" --python 3.12 --locked --extra dev
    prepend_path "$ROOT/.venv/bin"
    sudo npm install -g "openclaw@${OPENCLAW_VERSION}" "acpx@${ACPX_VERSION}"
    ;;
  *)
    echo "unsupported host OS for bootstrap: $HOST_OS" >&2
    exit 1
    ;;
esac

install_profile_assets

if [[ "$HOST_OS" == "Linux" && "$DOCKER_RUNTIME_INSTALLED_BY_BOOTSTRAP" -eq 1 ]]; then
  repair_linux_runtime_user_docker_access "$(id -un)"
fi

mkdir -p "$(strongclaw_data_dir)" "$(strongclaw_state_dir)" "$(strongclaw_log_dir)" "$(strongclaw_compose_state_dir)"
command -v openclaw >/dev/null 2>&1 || { echo "openclaw install failed" >&2; exit 1; }
command -v acpx >/dev/null 2>&1 || { echo "acpx install failed" >&2; exit 1; }
"$ROOT/scripts/bootstrap/render_openclaw_config.sh"
"$ROOT/scripts/bootstrap/doctor_host.sh"
mark_bootstrap_complete \
  "$OPENCLAW_CONFIG_PROFILE" \
  "$HOST_OS" \
  "$(id -un)" \
  "$(profile_bootstrap_capabilities "$OPENCLAW_CONFIG_PROFILE")"
echo "Bootstrap complete."
