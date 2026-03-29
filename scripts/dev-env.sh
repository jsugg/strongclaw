#!/usr/bin/env bash

# shellcheck disable=SC2317
_strongclaw_dev_env_return_or_exit() {
  local code="$1"
  return "$code" 2>/dev/null || exit "$code"
}

script_path="$0"
if [ -n "${BASH_SOURCE[0]:-}" ]; then
  script_path="${BASH_SOURCE[0]}"
fi
repo_root="$(CDPATH='' cd -- "$(dirname -- "$script_path")/.." && pwd)"
venv_activate="$repo_root/.venv/bin/activate"
runtime_root="${STRONGCLAW_RUNTIME_ROOT:-$repo_root/.local/dev-runtime}"

if [ ! -f "$repo_root/pyproject.toml" ] || [ ! -d "$repo_root/src/clawops" ]; then
  printf '%s\n' "scripts/dev-env.sh must live inside a StrongClaw source checkout." >&2
  _strongclaw_dev_env_return_or_exit 1
fi

case ":$PATH:" in
  *":$repo_root/bin:"*) ;;
  *) PATH="$repo_root/bin:$PATH" ;;
esac

export PATH
export STRONGCLAW_ASSET_ROOT="${STRONGCLAW_ASSET_ROOT:-$repo_root}"
export STRONGCLAW_RUNTIME_ROOT="$runtime_root"
export OPENCLAW_PROFILE="${OPENCLAW_PROFILE:-strongclaw-dev}"
export OPENCLAW_HOME="${OPENCLAW_HOME:-$runtime_root}"
export OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-$runtime_root/.openclaw}"
export OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-$OPENCLAW_STATE_DIR/openclaw.json}"
export OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-$OPENCLAW_CONFIG_PATH}"

if [ -f "$venv_activate" ]; then
  # shellcheck source=/dev/null
  . "$venv_activate"
fi
