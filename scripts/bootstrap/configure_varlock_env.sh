#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VARLOCK_ENV_DIR="${VARLOCK_ENV_DIR:-$ROOT/platform/configs/varlock}"
VARLOCK_LOCAL_ENV_FILE="${VARLOCK_LOCAL_ENV_FILE:-$VARLOCK_ENV_DIR/.env.local}"
VARLOCK_PLUGIN_ENV_FILE="${VARLOCK_PLUGIN_ENV_FILE:-$VARLOCK_ENV_DIR/.env.plugins}"
VARLOCK_ENV_TEMPLATE="${VARLOCK_ENV_TEMPLATE:-$VARLOCK_ENV_DIR/.env.local.example}"
VARLOCK_PLUGIN_VERSION_1PASSWORD="${VARLOCK_PLUGIN_VERSION_1PASSWORD:-0.3.0}"
VARLOCK_PLUGIN_VERSION_AWS="${VARLOCK_PLUGIN_VERSION_AWS:-0.0.5}"
VARLOCK_PLUGIN_VERSION_AZURE="${VARLOCK_PLUGIN_VERSION_AZURE:-0.0.5}"
VARLOCK_PLUGIN_VERSION_BITWARDEN="${VARLOCK_PLUGIN_VERSION_BITWARDEN:-0.0.5}"
VARLOCK_PLUGIN_VERSION_GCP="${VARLOCK_PLUGIN_VERSION_GCP:-0.2.0}"
VARLOCK_PLUGIN_VERSION_INFISICAL="${VARLOCK_PLUGIN_VERSION_INFISICAL:-0.0.5}"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/varlock.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/bootstrap_profiles.sh"

CHECK_ONLY=0
NON_INTERACTIVE=0
ENV_FILE_CREATED=0
MERGED_KEYS=0
AUTO_FILLED_VALUES=0
declare -a CONFIGURED_PROVIDER_KEYS=()
declare -a CONFIGURED_MODEL_CHAIN=()

usage() {
  cat <<'EOF'
Usage: configure_varlock_env.sh [--check-only] [--non-interactive]

Create, normalize, and validate the repo-local Varlock env contract used by
StrongClaw. In normal mode the script will:

1. create .env.local from the shipped example when missing
2. merge in newly added keys for older env files
3. generate safe defaults for required local secrets when blank
4. optionally configure a managed Varlock secret backend for provider auth
5. validate the final contract with Varlock when available

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

get_env_value_from_file() {
  local target_file="$1"
  local key="$2"
  if [[ ! -f "$target_file" ]]; then
    return 0
  fi
  grep -E "^${key}=" "$target_file" | tail -n 1 | cut -d= -f2- || true
}

get_env_value() {
  local key="$1"
  get_env_value_from_file "$VARLOCK_LOCAL_ENV_FILE" "$key"
}

set_env_value_in_file() {
  local target_file="$1"
  local key="$2"
  local value="$3"
  python3 - "$target_file" "$key" "$value" <<'PY'
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

set_env_value() {
  local key="$1"
  local value="$2"
  set_env_value_in_file "$VARLOCK_LOCAL_ENV_FILE" "$key" "$value"
}

clear_env_value() {
  local key="$1"
  set_env_value "$key" ""
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

local_ollama_model_available() {
  local requested_model="$1"
  local normalized_requested_model="${requested_model%:latest}"
  local listed_model normalized_listed_model
  if ! command -v ollama >/dev/null 2>&1; then
    return 1
  fi
  while IFS= read -r listed_model; do
    listed_model="$(trim_value "$listed_model")"
    [[ -n "$listed_model" ]] || continue
    normalized_listed_model="${listed_model%:latest}"
    if [[ "$normalized_listed_model" == "$normalized_requested_model" ]]; then
      return 0
    fi
  done < <(ollama list 2>/dev/null | awk 'NR > 1 {print $1}')
  return 1
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
  ensure_non_empty_value "VARLOCK_SECRET_BACKEND" "local" "Varlock secret backend"
  ensure_non_empty_value "OPENCLAW_GATEWAY_TOKEN" "$(generate_secret)" "gateway token"
  ensure_non_empty_value "OPENCLAW_CONTROL_USER" "$current_user" "runtime user"
  ensure_non_empty_value "OPENCLAW_STATE_DIR" "$default_state_dir" "OpenClaw state directory"
  ensure_non_empty_value "LITELLM_MASTER_KEY" "$(generate_secret)" "LiteLLM master key"
  ensure_non_empty_value "LITELLM_DB_PASSWORD" "$(generate_secret)" "LiteLLM database password"
  ensure_non_empty_value "HYPERMEMORY_EMBEDDING_BASE_URL" "http://127.0.0.1:4000/v1" "hypermemory embedding base URL"
  ensure_non_empty_value "HYPERMEMORY_QDRANT_URL" "http://127.0.0.1:6333" "hypermemory Qdrant URL"
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

current_secret_backend() {
  local backend
  backend="$(effective_env_value VARLOCK_SECRET_BACKEND)"
  if [[ -z "$backend" ]]; then
    backend="local"
  fi
  printf '%s' "$backend"
}

local_provider_credentials_present() {
  local key
  for key in \
    OPENAI_API_KEY \
    ANTHROPIC_API_KEY \
    ZAI_API_KEY \
    OPENROUTER_API_KEY \
    MOONSHOT_API_KEY \
    OLLAMA_API_KEY \
    OPENCLAW_OLLAMA_MODEL; do
    if value_is_effective "$(get_env_value "$key")"; then
      return 0
    fi
  done
  return 1
}

trim_value() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

append_unique_model() {
  local candidate
  local existing
  candidate="$(trim_value "$1")"
  [[ -n "$candidate" ]] || return 0
  if [[ "${#CONFIGURED_MODEL_CHAIN[@]}" -gt 0 ]]; then
    for existing in "${CONFIGURED_MODEL_CHAIN[@]}"; do
      if [[ "$existing" == "$candidate" ]]; then
        return 0
      fi
    done
  fi
  CONFIGURED_MODEL_CHAIN+=("$candidate")
}

append_unique_provider_key() {
  local candidate="$1"
  local existing
  [[ -n "$candidate" ]] || return 0
  if [[ "${#CONFIGURED_PROVIDER_KEYS[@]}" -gt 0 ]]; then
    for existing in "${CONFIGURED_PROVIDER_KEYS[@]}"; do
      if [[ "$existing" == "$candidate" ]]; then
        return 0
      fi
    done
  fi
  CONFIGURED_PROVIDER_KEYS+=("$candidate")
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

rebuild_model_chain_from_env() {
  local default_model fallback_csv item
  CONFIGURED_MODEL_CHAIN=()
  default_model="$(effective_env_value OPENCLAW_DEFAULT_MODEL)"
  fallback_csv="$(effective_env_value OPENCLAW_MODEL_FALLBACKS)"
  append_unique_model "$default_model"
  if [[ -n "$fallback_csv" ]]; then
    IFS=',' read -r -a _fallback_items <<<"$fallback_csv"
    for item in "${_fallback_items[@]}"; do
      append_unique_model "$item"
    done
  fi
}

rebuild_provider_keys_from_model_chain() {
  local model_ref provider_key
  CONFIGURED_PROVIDER_KEYS=()
  rebuild_model_chain_from_env
  if [[ "${#CONFIGURED_MODEL_CHAIN[@]}" -gt 0 ]]; then
    for model_ref in "${CONFIGURED_MODEL_CHAIN[@]}"; do
      provider_key="$(provider_key_for_model_ref "$model_ref")"
      if [[ -n "$provider_key" && "$provider_key" != "OLLAMA_API_KEY" ]]; then
        append_unique_provider_key "$provider_key"
      fi
    done
  fi
}

clear_backend_managed_local_values() {
  local provider_key
  if [[ "${#CONFIGURED_PROVIDER_KEYS[@]}" -gt 0 ]]; then
    for provider_key in "${CONFIGURED_PROVIDER_KEYS[@]}"; do
      clear_env_value "$provider_key"
    done
  fi
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

write_plugin_file() {
  local tmp_file="$VARLOCK_PLUGIN_ENV_FILE.tmp"
  mkdir -p "$(dirname "$VARLOCK_PLUGIN_ENV_FILE")"
  cat >"$tmp_file"
  mv "$tmp_file" "$VARLOCK_PLUGIN_ENV_FILE"
  chmod 600 "$VARLOCK_PLUGIN_ENV_FILE"
}

remove_plugin_file() {
  rm -f "$VARLOCK_PLUGIN_ENV_FILE"
}

plugin_config_present() {
  [[ -f "$VARLOCK_PLUGIN_ENV_FILE" ]]
}

configure_backend_local() {
  set_env_value "VARLOCK_SECRET_BACKEND" "local"
  clear_env_value "VARLOCK_SECRET_BACKEND_MODE"
  clear_env_value "VARLOCK_SECRET_BACKEND_AUTH"
  remove_plugin_file
}

configure_backend_1password() {
  local environment_id op_token account allow_app_auth
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    return 0
  fi
  if ! interactive_mode; then
    echo "ERROR: 1Password backend selection requires an interactive terminal to generate $VARLOCK_PLUGIN_ENV_FILE." >&2
    exit 1
  fi
  echo "== Varlock backend: 1Password =="
  echo "Recommended: use a 1Password Environment with a service account token."
  if prompt_yes_no "Use desktop app auth via the local op CLI instead?" "n"; then
    allow_app_auth="true"
    op_token=""
    account="$(prompt_value "Optional 1Password account shorthand" "")"
    environment_id="$(prompt_value "1Password Environment ID")"
    set_env_value "VARLOCK_SECRET_BACKEND_AUTH" "desktop-app"
  else
    allow_app_auth="false"
    op_token="$(prompt_value "1Password service account token" "" 1)"
    account="$(prompt_value "Optional 1Password account shorthand" "")"
    environment_id="$(prompt_value "1Password Environment ID")"
    set_env_value "VARLOCK_SECRET_BACKEND_AUTH" "service-account"
  fi
  set_env_value "VARLOCK_SECRET_BACKEND" "1password"
  set_env_value "VARLOCK_SECRET_BACKEND_MODE" "environment"
  clear_backend_managed_local_values
  write_plugin_file <<EOF
# @plugin(@varlock/1password-plugin@${VARLOCK_PLUGIN_VERSION_1PASSWORD})
# @initOp(token=\$OP_TOKEN, allowAppAuth=${allow_app_auth}${account:+, account=${account}})
# @setValuesBulk(opLoadEnvironment(${environment_id}))
# ---
# @type=opServiceAccountToken
OP_TOKEN=${op_token}
$(for provider_key in "${CONFIGURED_PROVIDER_KEYS[@]}"; do printf '%s=\n' "$provider_key"; done)
EOF
}

configure_backend_aws() {
  local store_type region name_prefix auth_mode access_key_id secret_access_key session_token profile resolver
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    return 0
  fi
  if ! interactive_mode; then
    echo "ERROR: AWS backend selection requires an interactive terminal to generate $VARLOCK_PLUGIN_ENV_FILE." >&2
    exit 1
  fi
  echo "== Varlock backend: AWS =="
  echo "  1. Secrets Manager"
  echo "  2. Systems Manager Parameter Store"
  while true; do
    store_type="$(prompt_value "Selection" "1")"
    case "$store_type" in
      1)
        set_env_value "VARLOCK_SECRET_BACKEND" "aws-secrets-manager"
        resolver="awsSecret"
        break
        ;;
      2)
        set_env_value "VARLOCK_SECRET_BACKEND" "aws-parameter-store"
        resolver="awsParam"
        break
        ;;
      *)
        echo "Please choose 1 or 2." >&2
        ;;
    esac
  done
  region="$(prompt_value "AWS region" "us-east-1")"
  name_prefix="$(prompt_value "Optional secret name prefix" "")"
  echo "Choose AWS auth mode:"
  echo "  1. Named profile from ~/.aws/credentials"
  echo "  2. Explicit access keys"
  while true; do
    auth_mode="$(prompt_value "Selection" "1")"
    case "$auth_mode" in
      1)
        profile="$(prompt_value "AWS profile name" "default")"
        set_env_value "VARLOCK_SECRET_BACKEND_AUTH" "aws-profile"
        break
        ;;
      2)
        access_key_id="$(prompt_value "AWS access key ID")"
        secret_access_key="$(prompt_value "AWS secret access key" "" 1)"
        session_token="$(prompt_value "Optional AWS session token" "" 1)"
        set_env_value "VARLOCK_SECRET_BACKEND_AUTH" "aws-access-key"
        break
        ;;
      *)
        echo "Please choose 1 or 2." >&2
        ;;
    esac
  done
  set_env_value "VARLOCK_SECRET_BACKEND_MODE" "$([[ "$resolver" == "awsSecret" ]] && echo secrets-manager || echo parameter-store)"
  clear_backend_managed_local_values
  write_plugin_file <<EOF
# @plugin(@varlock/aws-secrets-plugin@${VARLOCK_PLUGIN_VERSION_AWS})
# @initAws(region=${region}${name_prefix:+, namePrefix="${name_prefix}"}${profile:+, profile="${profile}"}${access_key_id:+, accessKeyId=\$AWS_ACCESS_KEY_ID}${secret_access_key:+, secretAccessKey=\$AWS_SECRET_ACCESS_KEY}${session_token:+, sessionToken=\$AWS_SESSION_TOKEN})
# ---
$(if [[ -n "$access_key_id" ]]; then cat <<'AUTH'
# @type=awsAccessKey
AWS_ACCESS_KEY_ID=
# @type=awsSecretKey
AWS_SECRET_ACCESS_KEY=
AUTH
fi)
$(if [[ -n "$session_token" ]]; then printf 'AWS_SESSION_TOKEN=\n'; fi)
$(for provider_key in "${CONFIGURED_PROVIDER_KEYS[@]}"; do printf '%s=%s()\n' "$provider_key" "$resolver"; done)
EOF
  if [[ -n "$access_key_id" ]]; then
    set_env_value_in_file "$VARLOCK_PLUGIN_ENV_FILE" "AWS_ACCESS_KEY_ID" "$access_key_id"
    set_env_value_in_file "$VARLOCK_PLUGIN_ENV_FILE" "AWS_SECRET_ACCESS_KEY" "$secret_access_key"
    if [[ -n "$session_token" ]]; then
      set_env_value_in_file "$VARLOCK_PLUGIN_ENV_FILE" "AWS_SESSION_TOKEN" "$session_token"
    fi
  fi
}

configure_backend_azure() {
  local vault_url auth_mode tenant_id client_id client_secret
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    return 0
  fi
  if ! interactive_mode; then
    echo "ERROR: Azure backend selection requires an interactive terminal to generate $VARLOCK_PLUGIN_ENV_FILE." >&2
    exit 1
  fi
  echo "== Varlock backend: Azure Key Vault =="
  vault_url="$(prompt_value "Azure Key Vault URL" "")"
  echo "Choose Azure auth mode:"
  echo "  1. Service principal"
  echo "  2. Azure CLI / managed identity"
  while true; do
    auth_mode="$(prompt_value "Selection" "1")"
    case "$auth_mode" in
      1)
        tenant_id="$(prompt_value "Azure tenant ID")"
        client_id="$(prompt_value "Azure client ID")"
        client_secret="$(prompt_value "Azure client secret" "" 1)"
        set_env_value "VARLOCK_SECRET_BACKEND_AUTH" "service-principal"
        break
        ;;
      2)
        set_env_value "VARLOCK_SECRET_BACKEND_AUTH" "azure-cli"
        break
        ;;
      *)
        echo "Please choose 1 or 2." >&2
        ;;
    esac
  done
  set_env_value "VARLOCK_SECRET_BACKEND" "azure-key-vault"
  clear_backend_managed_local_values
  write_plugin_file <<EOF
# @plugin(@varlock/azure-key-vault-plugin@${VARLOCK_PLUGIN_VERSION_AZURE})
# @initAzure(vaultUrl="${vault_url}"${tenant_id:+, tenantId=\$AZURE_TENANT_ID}${client_id:+, clientId=\$AZURE_CLIENT_ID}${client_secret:+, clientSecret=\$AZURE_CLIENT_SECRET})
# ---
$(if [[ -n "$tenant_id" ]]; then cat <<'AUTH'
# @type=azureTenantId
AZURE_TENANT_ID=
# @type=azureClientId
AZURE_CLIENT_ID=
# @type=azureClientSecret
AZURE_CLIENT_SECRET=
AUTH
fi)
$(for provider_key in "${CONFIGURED_PROVIDER_KEYS[@]}"; do printf '%s=azureSecret()\n' "$provider_key"; done)
EOF
  if [[ -n "$tenant_id" ]]; then
    set_env_value_in_file "$VARLOCK_PLUGIN_ENV_FILE" "AZURE_TENANT_ID" "$tenant_id"
    set_env_value_in_file "$VARLOCK_PLUGIN_ENV_FILE" "AZURE_CLIENT_ID" "$client_id"
    set_env_value_in_file "$VARLOCK_PLUGIN_ENV_FILE" "AZURE_CLIENT_SECRET" "$client_secret"
  fi
  clear_env_value "VARLOCK_SECRET_BACKEND_MODE"
}

configure_backend_google() {
  local project_id auth_mode service_account_json
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    return 0
  fi
  if ! interactive_mode; then
    echo "ERROR: Google Secret Manager selection requires an interactive terminal to generate $VARLOCK_PLUGIN_ENV_FILE." >&2
    exit 1
  fi
  echo "== Varlock backend: Google Secret Manager =="
  project_id="$(prompt_value "Google Cloud project ID" "")"
  echo "Choose Google auth mode:"
  echo "  1. Application Default Credentials"
  echo "  2. Service account JSON"
  while true; do
    auth_mode="$(prompt_value "Selection" "1")"
    case "$auth_mode" in
      1)
        set_env_value "VARLOCK_SECRET_BACKEND_AUTH" "gcp-adc"
        break
        ;;
      2)
        service_account_json="$(prompt_value "GCP service account JSON" "" 1)"
        set_env_value "VARLOCK_SECRET_BACKEND_AUTH" "gcp-service-account-json"
        break
        ;;
      *)
        echo "Please choose 1 or 2." >&2
        ;;
    esac
  done
  set_env_value "VARLOCK_SECRET_BACKEND" "google-secret-manager"
  clear_backend_managed_local_values
  write_plugin_file <<EOF
# @plugin(@varlock/google-secret-manager-plugin@${VARLOCK_PLUGIN_VERSION_GCP})
# @initGsm(projectId=${project_id}${service_account_json:+, credentials=\$GCP_SA_KEY})
# ---
$(if [[ -n "$service_account_json" ]]; then cat <<'AUTH'
# @type=gcpServiceAccountJson
GCP_SA_KEY=
AUTH
fi)
$(for provider_key in "${CONFIGURED_PROVIDER_KEYS[@]}"; do printf '%s=gsm()\n' "$provider_key"; done)
EOF
  if [[ -n "$service_account_json" ]]; then
    set_env_value_in_file "$VARLOCK_PLUGIN_ENV_FILE" "GCP_SA_KEY" "$service_account_json"
  fi
  clear_env_value "VARLOCK_SECRET_BACKEND_MODE"
}

configure_backend_infisical() {
  local project_id environment_name client_id client_secret site_url secret_path
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    return 0
  fi
  if ! interactive_mode; then
    echo "ERROR: Infisical backend selection requires an interactive terminal to generate $VARLOCK_PLUGIN_ENV_FILE." >&2
    exit 1
  fi
  echo "== Varlock backend: Infisical =="
  project_id="$(prompt_value "Infisical project ID" "")"
  environment_name="$(prompt_value "Infisical environment" "dev")"
  client_id="$(prompt_value "Infisical client ID")"
  client_secret="$(prompt_value "Infisical client secret" "" 1)"
  site_url="$(prompt_value "Optional Infisical site URL" "")"
  secret_path="$(prompt_value "Optional default secret path" "/")"
  set_env_value "VARLOCK_SECRET_BACKEND" "infisical"
  set_env_value "VARLOCK_SECRET_BACKEND_AUTH" "universal-auth"
  clear_env_value "VARLOCK_SECRET_BACKEND_MODE"
  clear_backend_managed_local_values
  write_plugin_file <<EOF
# @plugin(@varlock/infisical-plugin@${VARLOCK_PLUGIN_VERSION_INFISICAL})
# @initInfisical(projectId=${project_id}, environment=${environment_name}, clientId=\$INFISICAL_CLIENT_ID, clientSecret=\$INFISICAL_CLIENT_SECRET${site_url:+, siteUrl="${site_url}"}${secret_path:+, secretPath="${secret_path}"})
# @setValuesBulk(infisicalBulk())
# ---
# @type=infisicalClientId
INFISICAL_CLIENT_ID=${client_id}
# @type=infisicalClientSecret
INFISICAL_CLIENT_SECRET=${client_secret}
$(for provider_key in "${CONFIGURED_PROVIDER_KEYS[@]}"; do printf '%s=\n' "$provider_key"; done)
EOF
}

configure_backend_bitwarden() {
  local access_token api_url identity_url provider_key secret_uuid
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    return 0
  fi
  if ! interactive_mode; then
    echo "ERROR: Bitwarden backend selection requires an interactive terminal to generate $VARLOCK_PLUGIN_ENV_FILE." >&2
    exit 1
  fi
  echo "== Varlock backend: Bitwarden =="
  access_token="$(prompt_value "Bitwarden machine account access token" "" 1)"
  api_url="$(prompt_value "Optional Bitwarden API URL" "")"
  identity_url="$(prompt_value "Optional Bitwarden identity URL" "")"
  set_env_value "VARLOCK_SECRET_BACKEND" "bitwarden"
  set_env_value "VARLOCK_SECRET_BACKEND_AUTH" "machine-account"
  clear_env_value "VARLOCK_SECRET_BACKEND_MODE"
  clear_backend_managed_local_values
  write_plugin_file <<EOF
# @plugin(@varlock/bitwarden-plugin@${VARLOCK_PLUGIN_VERSION_BITWARDEN})
# @initBitwarden(accessToken=\$BITWARDEN_ACCESS_TOKEN${api_url:+, apiUrl="${api_url}"}${identity_url:+, identityUrl="${identity_url}"})
# ---
# @type=bitwardenAccessToken
BITWARDEN_ACCESS_TOKEN=${access_token}
EOF
  for provider_key in "${CONFIGURED_PROVIDER_KEYS[@]}"; do
    secret_uuid="$(prompt_value "Bitwarden secret UUID for ${provider_key}" "")"
    if [[ -n "$secret_uuid" ]]; then
      printf '%s=bitwarden("%s")\n' "$provider_key" "$secret_uuid" >>"$VARLOCK_PLUGIN_ENV_FILE"
    fi
  done
}

configure_selected_secret_backend() {
  local backend
  backend="$(current_secret_backend)"
  rebuild_provider_keys_from_model_chain
  case "$backend" in
    local) return 0 ;;
    1password) configure_backend_1password ;;
    aws-secrets-manager|aws-parameter-store) configure_backend_aws ;;
    azure-key-vault) configure_backend_azure ;;
    google-secret-manager) configure_backend_google ;;
    infisical) configure_backend_infisical ;;
    bitwarden) configure_backend_bitwarden ;;
    *)
      echo "ERROR: Unsupported VARLOCK_SECRET_BACKEND=${backend}." >&2
      exit 1
      ;;
  esac
}

plugin_file_covers_configured_provider_keys() {
  local provider_key
  if [[ ! -f "$VARLOCK_PLUGIN_ENV_FILE" ]]; then
    return 1
  fi
  for provider_key in "${CONFIGURED_PROVIDER_KEYS[@]}"; do
    if ! grep -q -E "^${provider_key}=" "$VARLOCK_PLUGIN_ENV_FILE"; then
      return 1
    fi
  done
  return 0
}

prompt_secret_backend_setup() {
  local selection backend
  backend="$(current_secret_backend)"
  if ! interactive_mode; then
    return 0
  fi
  if [[ "$backend" != "local" && -f "$VARLOCK_PLUGIN_ENV_FILE" ]]; then
    echo "Varlock secret backend: $backend"
    if ! prompt_yes_no "Review or change the configured secret backend?" "n"; then
      return 0
    fi
  elif local_provider_credentials_present; then
    if ! prompt_yes_no "Provider auth is already configured locally. Switch to a managed secret backend instead?" "n"; then
      return 0
    fi
  else
    echo "== Secret backend for provider auth =="
    echo "StrongClaw can keep provider keys in .env.local or fetch them via a supported Varlock plugin."
  fi

  echo "Choose the provider secret backend:"
  echo "  1. Repo-local .env.local"
  echo "  2. 1Password"
  echo "  3. AWS Secrets Manager"
  echo "  4. AWS Parameter Store"
  echo "  5. Azure Key Vault"
  echo "  6. Bitwarden Secrets Manager"
  echo "  7. Google Secret Manager"
  echo "  8. Infisical"

  while true; do
    selection="$(prompt_value "Selection" "1")"
    case "$selection" in
      1) configure_backend_local; return 0 ;;
      2) set_env_value "VARLOCK_SECRET_BACKEND" "1password"; return 0 ;;
      3) set_env_value "VARLOCK_SECRET_BACKEND" "aws-secrets-manager"; return 0 ;;
      4) set_env_value "VARLOCK_SECRET_BACKEND" "aws-parameter-store"; return 0 ;;
      5) set_env_value "VARLOCK_SECRET_BACKEND" "azure-key-vault"; return 0 ;;
      6) set_env_value "VARLOCK_SECRET_BACKEND" "bitwarden"; return 0 ;;
      7) set_env_value "VARLOCK_SECRET_BACKEND" "google-secret-manager"; return 0 ;;
      8) set_env_value "VARLOCK_SECRET_BACKEND" "infisical"; return 0 ;;
      *) echo "Please choose 1-8." >&2 ;;
    esac
  done
}

prompt_model_provider_setup() {
  local selection primary_model fallback_models fallback_model backend
  backend="$(current_secret_backend)"
  if ! interactive_mode; then
    return 0
  fi
  if [[ "$backend" == "local" ]] && local_provider_credentials_present; then
    return 0
  fi
  if value_is_effective "$(effective_env_value OPENCLAW_DEFAULT_MODEL)"; then
    rebuild_provider_keys_from_model_chain
    return 0
  fi

  if [[ "$backend" == "local" ]]; then
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
  else
    echo "== Model provider selection =="
    echo "Select the primary model chain. StrongClaw will wire the matching provider keys through $(current_secret_backend)."
  fi

  echo "Choose the primary provider:"
  echo "  1. OpenAI"
  echo "  2. Anthropic"
  echo "  3. Z.AI / GLM"
  echo "  4. OpenRouter"
  echo "  5. Moonshot"
  echo "  6. Ollama local model"
  echo "  7. Skip and rely on OpenClaw's interactive model setup"

  while true; do
    selection="$(prompt_value "Selection" "7")"
    case "$selection" in
      1) primary_model="openai/gpt-5.4"; break ;;
      2) primary_model="anthropic/claude-opus-4-6"; break ;;
      3) primary_model="zai/glm-5"; break ;;
      4) primary_model="$(prompt_value "OpenRouter primary model ref")"; break ;;
      5) primary_model="$(prompt_value "Moonshot primary model ref")"; break ;;
      6) primary_model="ollama/$(prompt_value "Ollama primary model" "$(effective_env_value OPENCLAW_OLLAMA_MODEL)")"; break ;;
      7) return 0 ;;
      *) echo "Please choose 1-7." >&2 ;;
    esac
  done

  if [[ -z "$primary_model" || "$primary_model" == "ollama/" ]]; then
    echo "ERROR: A concrete primary model ref is required." >&2
    return 1
  fi

  set_env_value "OPENCLAW_DEFAULT_MODEL" "$primary_model"
  fallback_models="$(prompt_value "Optional fallback model refs (comma-separated, blank to skip)" "$(effective_env_value OPENCLAW_MODEL_FALLBACKS)")"
  set_env_value "OPENCLAW_MODEL_FALLBACKS" "$fallback_models"
  rebuild_provider_keys_from_model_chain

  if [[ "$backend" == "local" ]]; then
    ensure_provider_credentials_for_model_ref "$primary_model"
    if [[ -n "$fallback_models" ]]; then
      IFS=',' read -r -a _fallback_items <<<"$fallback_models"
      for fallback_model in "${_fallback_items[@]}"; do
        fallback_model="$(trim_value "$fallback_model")"
        ensure_provider_credentials_for_model_ref "$fallback_model"
      done
    fi
    cat <<EOF
Stored provider auth hints in $VARLOCK_LOCAL_ENV_FILE.
OpenClaw model setup will use OPENCLAW_DEFAULT_MODEL=${primary_model}.
EOF
    return 0
  fi

  clear_backend_managed_local_values
}

ensure_hypermemory_embedding_model() {
  if ! profile_requires_hypermemory_backend "${OPENCLAW_CONFIG_PROFILE:-$STRONGCLAW_DEFAULT_PROFILE}"; then
    return 0
  fi

  local current_model
  current_model="$(effective_env_value HYPERMEMORY_EMBEDDING_MODEL)"
  if [[ -n "$current_model" ]]; then
    return 0
  fi

  if [[ "$CHECK_ONLY" -eq 0 ]] && ([[ "$NON_INTERACTIVE" -eq 1 ]] || ! interactive_mode); then
    if local_ollama_model_available "nomic-embed-text"; then
      set_env_value "HYPERMEMORY_EMBEDDING_MODEL" "ollama/nomic-embed-text"
      set_env_value "HYPERMEMORY_EMBEDDING_API_BASE" "http://host.docker.internal:11434"
      AUTO_FILLED_VALUES=$((AUTO_FILLED_VALUES + 1))
      echo "Configured HYPERMEMORY_EMBEDDING_MODEL=ollama/nomic-embed-text from local Ollama."
      echo "Configured HYPERMEMORY_EMBEDDING_API_BASE=http://host.docker.internal:11434 for the LiteLLM sidecar."
      return 0
    fi
  fi

  if [[ "$CHECK_ONLY" -eq 1 || "$NON_INTERACTIVE" -eq 1 ]] || ! interactive_mode; then
    echo "ERROR: HYPERMEMORY_EMBEDDING_MODEL is required when OPENCLAW_CONFIG_PROFILE=hypermemory." >&2
    echo "Set HYPERMEMORY_EMBEDDING_MODEL in $VARLOCK_LOCAL_ENV_FILE and rerun $ROOT/scripts/bootstrap/configure_varlock_env.sh." >&2
    exit 1
  fi

  echo "== Hypermemory embeddings =="
  echo "The hypermemory profile requires a LiteLLM embedding route target."
  set_or_prompt_value \
    "HYPERMEMORY_EMBEDDING_MODEL" \
    "Embedding model ref for LiteLLM route hypermemory-embedding" \
    "${current_model:-}"
  if ! value_is_effective "$(get_env_value HYPERMEMORY_EMBEDDING_MODEL)"; then
    echo "ERROR: HYPERMEMORY_EMBEDDING_MODEL is required for hypermemory." >&2
    exit 1
  fi
}

validate_secret_backend_configuration() {
  local backend auth_mode
  backend="$(current_secret_backend)"
  auth_mode="$(effective_env_value VARLOCK_SECRET_BACKEND_AUTH)"
  rebuild_provider_keys_from_model_chain
  if [[ "$backend" == "local" ]]; then
    if [[ -f "$VARLOCK_PLUGIN_ENV_FILE" ]]; then
      if [[ "$CHECK_ONLY" -eq 1 ]] || ! interactive_mode; then
        echo "ERROR: VARLOCK_SECRET_BACKEND=local, but $VARLOCK_PLUGIN_ENV_FILE still exists." >&2
        echo "Remove the plugin overlay or rerun $ROOT/scripts/bootstrap/configure_varlock_env.sh interactively to switch back to local secrets cleanly." >&2
        exit 1
      fi
      remove_plugin_file
      echo "Removed stale managed-backend overlay at $VARLOCK_PLUGIN_ENV_FILE."
    fi
    return 0
  fi
  if [[ ! -f "$VARLOCK_PLUGIN_ENV_FILE" ]]; then
    if [[ "$CHECK_ONLY" -eq 1 ]] || ! interactive_mode; then
      echo "ERROR: VARLOCK_SECRET_BACKEND=${backend}, but $VARLOCK_PLUGIN_ENV_FILE is missing." >&2
      echo "Run $ROOT/scripts/bootstrap/configure_varlock_env.sh in an interactive terminal to finish backend setup." >&2
      exit 1
    fi
    configure_selected_secret_backend
    return 0
  fi
  if ! plugin_file_covers_configured_provider_keys; then
    if [[ "$CHECK_ONLY" -eq 1 ]] || ! interactive_mode; then
      echo "ERROR: $VARLOCK_PLUGIN_ENV_FILE does not define resolver entries for the configured provider model chain." >&2
      echo "Run $ROOT/scripts/bootstrap/configure_varlock_env.sh to refresh the backend mapping." >&2
      exit 1
    fi
    configure_selected_secret_backend
  fi
  case "$auth_mode" in
    desktop-app)
      if ! command -v op >/dev/null 2>&1; then
        echo "ERROR: 1Password desktop app auth requires the \`op\` CLI on PATH." >&2
        exit 1
      fi
      ;;
    azure-cli)
      if ! command -v az >/dev/null 2>&1; then
        echo "ERROR: Azure CLI auth requires the \`az\` CLI on PATH." >&2
        exit 1
      fi
      ;;
    gcp-adc)
      if ! command -v gcloud >/dev/null 2>&1; then
        echo "ERROR: Google ADC auth requires the \`gcloud\` CLI on PATH." >&2
        exit 1
      fi
      ;;
  esac
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
    echo "Review $VARLOCK_LOCAL_ENV_FILE${VARLOCK_PLUGIN_ENV_FILE:+ and $VARLOCK_PLUGIN_ENV_FILE} and rerun:" >&2
    echo "  varlock load --path $VARLOCK_ENV_DIR" >&2
    exit 1
  fi
  echo "Validated Varlock env contract at $VARLOCK_LOCAL_ENV_FILE"
}

ensure_env_file_exists
merge_missing_keys_from_template
ensure_core_defaults
prompt_core_settings_review
prompt_secret_backend_setup
prompt_model_provider_setup
ensure_hypermemory_embedding_model
validate_secret_backend_configuration
validate_with_varlock

if interactive_mode; then
  cat <<EOF
Varlock env contract is ready.

What happens next:
- OpenClaw model/provider auth will be configured or verified during setup
- Host services can be activated safely with the prepared env contract
- You can edit $VARLOCK_LOCAL_ENV_FILE and $VARLOCK_PLUGIN_ENV_FILE manually at any time and rerun setup
EOF
fi
