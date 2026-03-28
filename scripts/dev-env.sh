#!/usr/bin/env sh

# shellcheck disable=SC2317
_strongclaw_dev_env_return_or_exit() {
  code="$1"
  return "$code" 2>/dev/null || exit "$code"
}

if [ ! -f "pyproject.toml" ] || [ ! -d "src/clawops" ]; then
  printf '%s\n' "source scripts/dev-env.sh from the StrongClaw repository root." >&2
  _strongclaw_dev_env_return_or_exit 1
fi

repo_root=$(pwd -P)
venv_activate="$repo_root/.venv/bin/activate"

if [ ! -f "$venv_activate" ]; then
  printf '%s\n' "Managed environment not found at $venv_activate. Run \`uv sync --locked\` first." >&2
  _strongclaw_dev_env_return_or_exit 1
fi

case ":$PATH:" in
  *":$repo_root/bin:"*) ;;
  *) PATH="$repo_root/bin:$PATH" ;;
esac

export PATH
export STRONGCLAW_ASSET_ROOT="$repo_root"

# shellcheck source=/dev/null
. "$venv_activate"
