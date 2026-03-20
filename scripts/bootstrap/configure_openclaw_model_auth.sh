#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-$HOME/.openclaw/openclaw.json}"
MODEL_SETUP_MODE="${OPENCLAW_MODEL_SETUP_MODE:-auto}"
VARLOCK_ENV_DIR="${OPENCLAW_VARLOCK_ENV_PATH:-$ROOT/platform/configs/varlock}"
VARLOCK_LOCAL_ENV_FILE="${VARLOCK_LOCAL_ENV_FILE:-$VARLOCK_ENV_DIR/.env.local}"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/openclaw.sh"

CHECK_ONLY=0
PROBE=0
PROBE_MAX_TOKENS="${OPENCLAW_MODEL_PROBE_MAX_TOKENS:-16}"
MODELS_STATUS_SUPPORTED=0
VARLOCK_ENV_SNAPSHOT=""
declare -a MODEL_CHAIN=()

usage() {
  cat <<'EOF'
Usage: configure_openclaw_model_auth.sh [--check-only] [--probe] [--probe-max-tokens N]

Ensure the rendered OpenClaw config has at least one available model for every
configured agent. In normal mode the script will:

1. accept already-working model auth
2. apply env-driven model defaults when explicit values exist
3. launch `openclaw configure --section model` on an interactive terminal

Use --check-only to fail fast without mutating config or prompting.
Use --probe to perform a live provider probe after configuration. This can
consume a small model request and should be reserved for setup/doctor flows.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only)
      CHECK_ONLY=1
      shift
      ;;
    --probe)
      PROBE=1
      shift
      ;;
    --probe-max-tokens)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --probe-max-tokens requires a value." >&2
        exit 1
      fi
      PROBE_MAX_TOKENS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

require_openclaw "OpenClaw model auth configuration requires the OpenClaw CLI."
command -v jq >/dev/null 2>&1 || {
  echo "ERROR: OpenClaw model auth configuration requires jq." >&2
  exit 1
}

if [[ ! -f "$OPENCLAW_CONFIG" ]]; then
  echo "ERROR: Rendered OpenClaw config not found at $OPENCLAW_CONFIG." >&2
  exit 1
fi

get_env_value() {
  local key="$1"
  if [[ ! -f "$VARLOCK_LOCAL_ENV_FILE" ]]; then
    return 0
  fi
  grep -E "^${key}=" "$VARLOCK_LOCAL_ENV_FILE" | tail -n 1 | cut -d= -f2- || true
}

capture_varlock_env_snapshot() {
  if [[ -n "$VARLOCK_ENV_SNAPSHOT" ]]; then
    return 0
  fi
  if ! varlock_is_available || [[ ! -d "$VARLOCK_ENV_DIR" ]]; then
    return 0
  fi
  VARLOCK_ENV_SNAPSHOT="$(mktemp)"
  if ! run_varlock run --path "$VARLOCK_ENV_DIR" -- env >"$VARLOCK_ENV_SNAPSHOT" 2>/dev/null; then
    rm -f "$VARLOCK_ENV_SNAPSHOT"
    VARLOCK_ENV_SNAPSHOT=""
    return 0
  fi
}

get_effective_env_value() {
  local key="$1"
  local resolved_value=""
  capture_varlock_env_snapshot
  if [[ -n "$VARLOCK_ENV_SNAPSHOT" && -f "$VARLOCK_ENV_SNAPSHOT" ]]; then
    resolved_value="$(grep -E "^${key}=" "$VARLOCK_ENV_SNAPSHOT" | tail -n 1 | cut -d= -f2- || true)"
    if [[ -n "$resolved_value" ]]; then
      printf '%s' "$resolved_value"
      return 0
    fi
  fi
  get_env_value "$key"
}

trap 'if [[ -n "${VARLOCK_ENV_SNAPSHOT:-}" ]]; then rm -f "$VARLOCK_ENV_SNAPSHOT"; fi' EXIT

list_agent_ids() {
  run_openclaw agents list --json | jq -r '.[].id'
}

detect_models_status_support() {
  if run_openclaw models status --help >/dev/null 2>&1; then
    MODELS_STATUS_SUPPORTED=1
    return 0
  fi
  MODELS_STATUS_SUPPORTED=0
}

agent_has_available_model_via_list() {
  local agent_id="$1"
  run_openclaw models --agent "$agent_id" list --json |
    jq -e '.models | any(.available == true)' >/dev/null
}

agent_model_status_ok() {
  local agent_id="$1"
  local status_args=(models status --agent "$agent_id" --check)
  if [[ "$PROBE" -eq 1 ]]; then
    status_args+=(--probe --probe-max-tokens "$PROBE_MAX_TOKENS")
  fi
  run_openclaw "${status_args[@]}" >/dev/null
}

all_agents_have_available_models() {
  local agent_id
  local missing=0
  local found_agent=0
  while IFS= read -r agent_id; do
    [[ -n "$agent_id" ]] || continue
    found_agent=1
    if [[ "$MODELS_STATUS_SUPPORTED" -eq 1 ]]; then
      if ! agent_model_status_ok "$agent_id"; then
        printf 'OpenClaw agent %s has no healthy model/provider configuration.\n' "$agent_id" >&2
        missing=1
      fi
      continue
    fi
    if ! agent_has_available_model_via_list "$agent_id"; then
      printf 'OpenClaw agent %s has no available configured models.\n' "$agent_id" >&2
      missing=1
    fi
  done < <(list_agent_ids)
  if [[ "$found_agent" -eq 0 ]]; then
    echo "OpenClaw does not have any configured agents to validate." >&2
    return 1
  fi
  [[ "$missing" -eq 0 ]]
}

append_model_candidate() {
  local candidate="$1"
  local existing
  [[ -n "$candidate" ]] || return 0
  if [[ "${#MODEL_CHAIN[@]}" -gt 0 ]]; then
    for existing in "${MODEL_CHAIN[@]}"; do
      if [[ "$existing" == "$candidate" ]]; then
        return 0
      fi
    done
  fi
  MODEL_CHAIN+=("$candidate")
}

split_csv_candidates() {
  local csv="$1"
  local item
  IFS=',' read -r -a _csv_items <<<"$csv"
  for item in "${_csv_items[@]}"; do
    item="${item#"${item%%[![:space:]]*}"}"
    item="${item%"${item##*[![:space:]]}"}"
    append_model_candidate "$item"
  done
}

build_model_chain() {
  local default_model fallback_csv
  default_model="$(get_effective_env_value OPENCLAW_DEFAULT_MODEL)"
  fallback_csv="$(get_effective_env_value OPENCLAW_MODEL_FALLBACKS)"
  if [[ -n "$default_model" ]]; then
    append_model_candidate "$default_model"
    split_csv_candidates "$fallback_csv"
    return 0
  fi
  if [[ -n "$(get_effective_env_value OPENAI_API_KEY)" ]]; then
    append_model_candidate "openai/gpt-5.4"
  fi
  if [[ -n "$(get_effective_env_value ANTHROPIC_API_KEY)" ]]; then
    append_model_candidate "anthropic/claude-opus-4-6"
  fi
  if [[ -n "$(get_effective_env_value ZAI_API_KEY)" ]]; then
    append_model_candidate "zai/glm-5"
  fi
  if [[ -n "$(get_effective_env_value OLLAMA_API_KEY)" ]]; then
    local ollama_model
    ollama_model="$(get_effective_env_value OPENCLAW_OLLAMA_MODEL)"
    if [[ -n "$ollama_model" ]]; then
      append_model_candidate "ollama/$ollama_model"
    fi
  fi
}

apply_model_chain() {
  local primary="$1"
  shift
  local fallbacks=("$@")
  local agent_id

  while IFS= read -r agent_id; do
    [[ -n "$agent_id" ]] || continue
    run_openclaw models --agent "$agent_id" set "$primary" >/dev/null
    run_openclaw models --agent "$agent_id" fallbacks clear >/dev/null
    local fallback
    for fallback in "${fallbacks[@]}"; do
      run_openclaw models --agent "$agent_id" fallbacks add "$fallback" >/dev/null
    done
  done < <(list_agent_ids)
}

print_guidance() {
  cat >&2 <<EOF
ERROR: OpenClaw does not have a usable assistant model yet.

StrongClaw setup now requires model/provider auth before the gateway is treated as healthy.

Supported setup paths:
- Guided wizard: rerun this step in a terminal and complete \`openclaw configure --section model\`
- Direct provider auth:
  - Generic OAuth/device flow: \`openclaw models auth login --provider <id>\`
  - Setup-token flow: \`openclaw models auth setup-token --provider <id>\`
- Env-driven: set provider auth in $VARLOCK_LOCAL_ENV_FILE and optionally:
  - OPENCLAW_DEFAULT_MODEL=openai/gpt-5.4
  - OPENCLAW_MODEL_FALLBACKS=anthropic/claude-opus-4-6,zai/glm-5
  - OLLAMA_API_KEY=ollama-local with OPENCLAW_OLLAMA_MODEL=<pulled-model>

Recognized provider keys in the Varlock env contract:
- OPENAI_API_KEY -> openai/gpt-5.4
- ANTHROPIC_API_KEY -> anthropic/claude-opus-4-6
- ZAI_API_KEY -> zai/glm-5
EOF
}

detect_models_status_support

if all_agents_have_available_models; then
  echo "OpenClaw model auth is already usable."
  exit 0
fi

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  print_guidance
  exit 1
fi

if [[ "$MODEL_SETUP_MODE" == "skip" ]]; then
  echo "Skipping OpenClaw model auth configuration because OPENCLAW_MODEL_SETUP_MODE=skip."
  exit 0
fi

MODEL_CHAIN=()
build_model_chain
if [[ "${#MODEL_CHAIN[@]}" -gt 0 ]]; then
  apply_model_chain "${MODEL_CHAIN[0]}" "${MODEL_CHAIN[@]:1}"
  if all_agents_have_available_models; then
    printf 'Configured OpenClaw model chain: %s\n' "${MODEL_CHAIN[*]}"
    exit 0
  fi
fi

if [[ "$MODEL_SETUP_MODE" == "auto" || "$MODEL_SETUP_MODE" == "prompt" ]]; then
  if [[ -t 0 && -t 1 ]]; then
    echo "== OpenClaw model configuration =="
    echo "Launching interactive model/provider setup because no usable model is configured."
    run_openclaw configure --section model
    if all_agents_have_available_models; then
      echo "OpenClaw model auth configured successfully."
      exit 0
    fi
  fi
fi

print_guidance
exit 1
