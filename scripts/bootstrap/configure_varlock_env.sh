#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VARLOCK_ENV_DIR="${VARLOCK_ENV_DIR:-$ROOT/platform/configs/varlock}"
VARLOCK_LOCAL_ENV_FILE="${VARLOCK_LOCAL_ENV_FILE:-$VARLOCK_ENV_DIR/.env.local}"
VARLOCK_ENV_TEMPLATE="${VARLOCK_ENV_TEMPLATE:-$VARLOCK_ENV_DIR/.env.local.example}"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/varlock.sh"

CHECK_ONLY=0
NON_INTERACTIVE=0
ENV_FILE_CREATED=0
MERGED_KEYS=0
AUTO_FILLED_VALUES=0

usage() {
  cat <<'EOF'
Usage: configure_varlock_env.sh [--check-only] [--non-interactive]

Create, normalize, and validate the repo-local Varlock env contract used by
StrongClaw. In normal mode the script will:

1. create .env.local from the shipped example when missing
2. merge in newly added keys for older env files
3. generate safe defaults for required local secrets when blank
4. validate the final contract with Varlock when available

Use --check-only to report readiness without mutating files.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only)
      CHECK_ONLY=1
      shift
      ;;
    --non-interactive)
      NON_INTERACTIVE=1
      shift
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

has_tty() {
  [[ -t 0 && -t 1 ]]
}

interactive_mode() {
  [[ "$CHECK_ONLY" -eq 0 && "$NON_INTERACTIVE" -eq 0 ]] && has_tty
}

is_placeholder_value() {
  local value="${1:-}"
  case "$value" in
    "" | "replace-with-"* | "changeme"* | "your-"* | "<"*">") return 0 ;;
    *) return 1 ;;
  esac
}

value_is_effective() {
  local value="${1:-}"
  ! is_placeholder_value "$value"
}

generate_secret() {
  if command -v python3 >/dev/null 2>&1; then
    python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
    return 0
  fi
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
    return 0
  fi
  date +%s%N | shasum -a 256 | awk '{print $1}'
}

ensure_env_file_exists() {
  if [[ -f "$VARLOCK_LOCAL_ENV_FILE" ]]; then
    return 0
  fi
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    echo "ERROR: Varlock local env contract not found at $VARLOCK_LOCAL_ENV_FILE." >&2
    echo "Run $ROOT/scripts/bootstrap/setup.sh to create and configure it." >&2
    exit 1
  fi
  if [[ ! -f "$VARLOCK_ENV_TEMPLATE" ]]; then
    echo "ERROR: Varlock env template not found at $VARLOCK_ENV_TEMPLATE." >&2
    exit 1
  fi
  mkdir -p "$(dirname "$VARLOCK_LOCAL_ENV_FILE")"
  cp "$VARLOCK_ENV_TEMPLATE" "$VARLOCK_LOCAL_ENV_FILE"
  chmod 600 "$VARLOCK_LOCAL_ENV_FILE"
  ENV_FILE_CREATED=1
  echo "Created Varlock env contract at $VARLOCK_LOCAL_ENV_FILE from the shipped example."
}

get_env_value() {
  local key="$1"
  if [[ ! -f "$VARLOCK_LOCAL_ENV_FILE" ]]; then
    return 0
  fi
  grep -E "^${key}=" "$VARLOCK_LOCAL_ENV_FILE" | tail -n 1 | cut -d= -f2- || true
}

set_env_value() {
  local key="$1"
  local value="$2"
  python3 - "$VARLOCK_LOCAL_ENV_FILE" "$key" "$value" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines()
needle = f"{key}="
updated = False
for index, line in enumerate(lines):
    if line.startswith(needle):
        lines[index] = f"{needle}{value}"
        updated = True
        break
if not updated:
    lines.append(f"{needle}{value}")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

merge_missing_keys_from_template() {
  local line key
  if [[ ! -f "$VARLOCK_ENV_TEMPLATE" ]]; then
    if [[ "$CHECK_ONLY" -eq 0 ]]; then
      echo "Varlock env template not found at $VARLOCK_ENV_TEMPLATE; skipping template key merge."
    fi
    return 0
  fi
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    [[ "$line" == \#* ]] && continue
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    if ! grep -q -E "^${key}=" "$VARLOCK_LOCAL_ENV_FILE"; then
      if [[ "$CHECK_ONLY" -eq 1 ]]; then
        echo "ERROR: Varlock env contract is missing key ${key} in $VARLOCK_LOCAL_ENV_FILE." >&2
        echo "Run $ROOT/scripts/bootstrap/configure_varlock_env.sh to merge missing keys." >&2
        exit 1
      fi
      printf '%s\n' "$line" >>"$VARLOCK_LOCAL_ENV_FILE"
      MERGED_KEYS=$((MERGED_KEYS + 1))
    fi
  done <"$VARLOCK_ENV_TEMPLATE"
  if [[ "$MERGED_KEYS" -gt 0 ]]; then
    echo "Merged newly shipped env keys into $VARLOCK_LOCAL_ENV_FILE."
  fi
}

ensure_non_empty_value() {
  local key="$1"
  local default_value="$2"
  local label="$3"
  local current
  current="$(get_env_value "$key")"
  if value_is_effective "$current"; then
    return 0
  fi
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    echo "ERROR: Required Varlock key ${key} is blank or still uses a placeholder in $VARLOCK_LOCAL_ENV_FILE." >&2
    echo "Run $ROOT/scripts/bootstrap/configure_varlock_env.sh to populate it." >&2
    exit 1
  fi
  set_env_value "$key" "$default_value"
  AUTO_FILLED_VALUES=$((AUTO_FILLED_VALUES + 1))
  echo "Configured ${label}."
}

prompt_value() {
  local prompt="$1"
  local default_value="${2:-}"
  local secret="${3:-0}"
  local answer=""
  if [[ "$secret" -eq 1 ]]; then
    if [[ -n "$default_value" ]]; then
      printf '%s [configured; press Enter to keep]: ' "$prompt" >&2
    else
      printf '%s: ' "$prompt" >&2
    fi
    IFS= read -r -s answer || true
    printf '\n' >&2
  else
    if [[ -n "$default_value" ]]; then
      printf '%s [%s]: ' "$prompt" "$default_value" >&2
    else
      printf '%s: ' "$prompt" >&2
    fi
    IFS= read -r answer || true
  fi
  if [[ -z "$answer" ]]; then
    answer="$default_value"
  fi
  printf '%s' "$answer"
}

prompt_yes_no() {
  local prompt="$1"
  local default_answer="${2:-y}"
  local answer=""
  while true; do
    if [[ "$default_answer" == "y" ]]; then
      printf '%s [Y/n]: ' "$prompt" >&2
    else
      printf '%s [y/N]: ' "$prompt" >&2
    fi
    IFS= read -r answer || true
    answer="${answer,,}"
    if [[ -z "$answer" ]]; then
      answer="$default_answer"
    fi
    case "$answer" in
      y | yes) return 0 ;;
      n | no) return 1 ;;
      *) echo "Please answer y or n." >&2 ;;
    esac
  done
}

effective_env_value() {
  local current
  current="$(get_env_value "$1")"
  if value_is_effective "$current"; then
    printf '%s' "$current"
  fi
}

set_or_prompt_value() {
  local key="$1"
  local prompt="$2"
  local default_value="$3"
  local secret="${4:-0}"
  local answer
  answer="$(prompt_value "$prompt" "$default_value" "$secret")"
  if [[ -n "$answer" ]]; then
    set_env_value "$key" "$answer"
  fi
}

ensure_core_defaults() {
  local current_user
  # shellcheck disable=SC2088
  local default_state_dir="~/.openclaw"
  # shellcheck disable=SC2088
  local default_whatsapp_dir="~/.openclaw/channels/whatsapp"
  current_user="${USER:-$(id -un)}"
  ensure_non_empty_value "APP_ENV" "local" "APP_ENV=local"
  ensure_non_empty_value "OPENCLAW_VERSION" "${OPENCLAW_VERSION:-2026.3.13}" "OpenClaw version"
  ensure_non_empty_value "OPENCLAW_GATEWAY_TOKEN" "$(generate_secret)" "gateway token"
  ensure_non_empty_value "OPENCLAW_CONTROL_USER" "$current_user" "runtime user"
  ensure_non_empty_value "OPENCLAW_STATE_DIR" "$default_state_dir" "OpenClaw state directory"
  ensure_non_empty_value "LITELLM_MASTER_KEY" "$(generate_secret)" "LiteLLM master key"
  ensure_non_empty_value "LITELLM_DB_PASSWORD" "$(generate_secret)" "LiteLLM database password"
  ensure_non_empty_value "WHATSAPP_SESSION_DIR" "$default_whatsapp_dir" "WhatsApp session directory"
}

prompt_core_settings_review() {
  local current_user default_runtime_user default_state_dir
  # shellcheck disable=SC2088
  local default_home_state_dir="~/.openclaw"
  if ! interactive_mode; then
    return 0
  fi
  if [[ "$ENV_FILE_CREATED" -eq 0 && "$AUTO_FILLED_VALUES" -eq 0 && "$MERGED_KEYS" -eq 0 ]]; then
    return 0
  fi

  current_user="${USER:-$(id -un)}"
  default_runtime_user="$(effective_env_value OPENCLAW_CONTROL_USER)"
  if [[ -z "$default_runtime_user" ]]; then
    default_runtime_user="$current_user"
  fi
  default_state_dir="$(effective_env_value OPENCLAW_STATE_DIR)"
  if [[ -z "$default_state_dir" ]]; then
    default_state_dir="$default_home_state_dir"
  fi

  echo "== Varlock runtime settings =="
  echo "Review the repo-local runtime values. Press Enter to keep the shown default."
  set_or_prompt_value "OPENCLAW_CONTROL_USER" "OpenClaw runtime user" "$default_runtime_user"
  set_or_prompt_value "OPENCLAW_STATE_DIR" "OpenClaw state directory" "$default_state_dir"
}

provider_env_present() {
  local key
  for key in \
    OPENAI_API_KEY \
    ANTHROPIC_API_KEY \
    ZAI_API_KEY \
    OPENROUTER_API_KEY \
    MOONSHOT_API_KEY \
    OLLAMA_API_KEY \
    OPENCLAW_OLLAMA_MODEL \
    OPENCLAW_DEFAULT_MODEL \
    OPENCLAW_MODEL_FALLBACKS; do
    if value_is_effective "$(get_env_value "$key")"; then
      return 0
    fi
  done
  return 1
}

provider_key_for_model_ref() {
  local model_ref="$1"
  case "${model_ref%%/*}" in
    openai) printf 'OPENAI_API_KEY' ;;
    anthropic) printf 'ANTHROPIC_API_KEY' ;;
    zai) printf 'ZAI_API_KEY' ;;
    openrouter) printf 'OPENROUTER_API_KEY' ;;
    moonshot) printf 'MOONSHOT_API_KEY' ;;
    ollama) printf 'OLLAMA_API_KEY' ;;
    *) printf '' ;;
  esac
}

ensure_provider_credentials_for_model_ref() {
  local model_ref="$1"
  local provider key current_value model_name
  [[ -n "$model_ref" ]] || return 0
  provider="${model_ref%%/*}"
  key="$(provider_key_for_model_ref "$model_ref")"
  case "$provider" in
    openai)
      current_value="$(effective_env_value OPENAI_API_KEY)"
      set_or_prompt_value "OPENAI_API_KEY" "OpenAI API key" "$current_value" 1
      ;;
    anthropic)
      current_value="$(effective_env_value ANTHROPIC_API_KEY)"
      set_or_prompt_value "ANTHROPIC_API_KEY" "Anthropic API key" "$current_value" 1
      ;;
    zai)
      current_value="$(effective_env_value ZAI_API_KEY)"
      set_or_prompt_value "ZAI_API_KEY" "Z.AI API key" "$current_value" 1
      ;;
    openrouter)
      current_value="$(effective_env_value OPENROUTER_API_KEY)"
      set_or_prompt_value "OPENROUTER_API_KEY" "OpenRouter API key" "$current_value" 1
      ;;
    moonshot)
      current_value="$(effective_env_value MOONSHOT_API_KEY)"
      set_or_prompt_value "MOONSHOT_API_KEY" "Moonshot API key" "$current_value" 1
      ;;
    ollama)
      model_name="${model_ref#ollama/}"
      if [[ "$model_name" == "$model_ref" || -z "$model_name" ]]; then
        model_name="$(effective_env_value OPENCLAW_OLLAMA_MODEL)"
      fi
      model_name="$(prompt_value "Ollama model name" "$model_name")"
      if [[ -n "$model_name" ]]; then
        set_env_value "OPENCLAW_OLLAMA_MODEL" "$model_name"
        set_env_value "OPENCLAW_DEFAULT_MODEL" "ollama/$model_name"
      fi
      if ! value_is_effective "$(get_env_value OLLAMA_API_KEY)"; then
        set_env_value "OLLAMA_API_KEY" "ollama-local"
        echo "Configured OLLAMA_API_KEY=ollama-local."
      fi
      ;;
    *)
      if [[ -n "$key" ]]; then
        return 0
      fi
      cat <<EOF
This guided Varlock prompt does not manage auth for ${provider} models directly.
Continue setup and use OpenClaw's interactive model auth for ${model_ref}.
EOF
      ;;
  esac
}

prompt_provider_auth_setup() {
  local selection primary_model fallback_models fallback_model
  if ! interactive_mode; then
    return 0
  fi
  if provider_env_present; then
    return 0
  fi

  echo "== Provider auth in Varlock =="
  cat <<'EOF'
No model/provider auth is stored in the repo-local Varlock env yet.

You can:
- save API-key-based or Ollama auth into platform/configs/varlock/.env.local now
- or leave auth out of Varlock and continue to OpenClaw's interactive model setup next
EOF
  if ! prompt_yes_no "Configure env-based provider auth now?" "n"; then
    return 0
  fi

  echo "Choose the primary provider for env-based auth:"
  echo "  1. OpenAI API key"
  echo "  2. Anthropic API key"
  echo "  3. Z.AI / GLM API key"
  echo "  4. OpenRouter API key"
  echo "  5. Moonshot API key"
  echo "  6. Ollama local model"
  echo "  7. Skip env-based auth and continue to OpenClaw model setup"

  while true; do
    selection="$(prompt_value "Selection" "7")"
    case "$selection" in
      1)
        primary_model="openai/gpt-5.4"
        break
        ;;
      2)
        primary_model="anthropic/claude-opus-4-6"
        break
        ;;
      3)
        primary_model="zai/glm-5"
        break
        ;;
      4)
        primary_model="$(prompt_value "OpenRouter primary model ref")"
        break
        ;;
      5)
        primary_model="$(prompt_value "Moonshot primary model ref")"
        break
        ;;
      6)
        primary_model="ollama/$(prompt_value "Ollama primary model" "$(effective_env_value OPENCLAW_OLLAMA_MODEL)")"
        break
        ;;
      7)
        return 0
        ;;
      *)
        echo "Please choose 1-7." >&2
        ;;
    esac
  done

  if [[ -z "$primary_model" || "$primary_model" == "ollama/" ]]; then
    echo "ERROR: A concrete primary model ref is required for env-based provider auth." >&2
    return 1
  fi

  set_env_value "OPENCLAW_DEFAULT_MODEL" "$primary_model"
  ensure_provider_credentials_for_model_ref "$primary_model"

  fallback_models="$(prompt_value "Optional fallback model refs (comma-separated, blank to skip)" "$(effective_env_value OPENCLAW_MODEL_FALLBACKS)")"
  set_env_value "OPENCLAW_MODEL_FALLBACKS" "$fallback_models"
  if [[ -n "$fallback_models" ]]; then
    IFS=',' read -r -a _fallback_items <<<"$fallback_models"
    for fallback_model in "${_fallback_items[@]}"; do
      fallback_model="${fallback_model#"${fallback_model%%[![:space:]]*}"}"
      fallback_model="${fallback_model%"${fallback_model##*[![:space:]]}"}"
      ensure_provider_credentials_for_model_ref "$fallback_model"
    done
  fi

  cat <<EOF
Stored provider auth hints in $VARLOCK_LOCAL_ENV_FILE.
OpenClaw model setup will use OPENCLAW_DEFAULT_MODEL=${primary_model}.
EOF
}

validate_with_varlock() {
  if ! varlock_is_available; then
    if [[ "$CHECK_ONLY" -eq 1 ]]; then
      echo "ERROR: Varlock is required to validate the env contract." >&2
      exit 1
    fi
    echo "Varlock is not installed yet; env file was prepared, but validation is deferred."
    echo "Next step: run $ROOT/scripts/bootstrap/bootstrap.sh or $ROOT/scripts/bootstrap/setup.sh."
    return 0
  fi
  if ! run_varlock load --path "$VARLOCK_ENV_DIR" >/dev/null; then
    echo "ERROR: Varlock failed to validate the env contract in $VARLOCK_ENV_DIR." >&2
    echo "Review $VARLOCK_LOCAL_ENV_FILE and rerun:" >&2
    echo "  varlock load --path $VARLOCK_ENV_DIR" >&2
    exit 1
  fi
  echo "Validated Varlock env contract at $VARLOCK_LOCAL_ENV_FILE"
}

ensure_env_file_exists
merge_missing_keys_from_template
ensure_core_defaults
prompt_core_settings_review
prompt_provider_auth_setup
validate_with_varlock

if interactive_mode; then
  cat <<EOF
Varlock env contract is ready.

What happens next:
- OpenClaw model/provider auth will be configured or verified during setup
- Host services can be activated safely with the prepared env contract
- You can edit $VARLOCK_LOCAL_ENV_FILE manually at any time and rerun setup
EOF
fi
