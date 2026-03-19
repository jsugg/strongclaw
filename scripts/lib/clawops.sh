#!/usr/bin/env bash
set -euo pipefail

resolve_clawops_bin() {
  local root="$1"
  if [[ -n "${CLAWOPS_BIN:-}" ]]; then
    printf '%s\n' "$CLAWOPS_BIN"
    return 0
  fi
  if [[ "${CLAWOPS_PREFER_PATH:-0}" == "1" ]] && command -v clawops >/dev/null 2>&1; then
    command -v clawops
    return 0
  fi
  local venv_bin="$root/.venv/bin/clawops"
  if [[ -x "$venv_bin" ]]; then
    printf '%s\n' "$venv_bin"
    return 0
  fi
  if command -v clawops >/dev/null 2>&1; then
    command -v clawops
    return 0
  fi
  echo "clawops executable not found. Run 'make install' or 'uv sync --locked --extra dev' in $root." >&2
  return 1
}

prepend_clawops_venv_path() {
  local root="$1"
  local venv_dir="$root/.venv/bin"
  if [[ ! -d "$venv_dir" ]]; then
    return 0
  fi
  case ":$PATH:" in
    *":$venv_dir:"*) ;;
    *) PATH="$venv_dir:$PATH" ;;
  esac
}

run_clawops() {
  local root="$1"
  shift
  local clawops_bin
  clawops_bin="$(resolve_clawops_bin "$root")"
  "$clawops_bin" "$@"
}

run_shell_entrypoint() {
  local script_path="$1"
  shift
  if [[ ! -f "$script_path" ]]; then
    echo "shell entrypoint not found: $script_path" >&2
    return 1
  fi
  /bin/bash "$script_path" "$@"
}
