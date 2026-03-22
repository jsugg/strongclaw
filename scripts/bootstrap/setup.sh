#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/clawops.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/docker_runtime.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/bootstrap_profiles.sh"
BOOTSTRAP_SCRIPT="${BOOTSTRAP_SCRIPT:-$ROOT/scripts/bootstrap/bootstrap.sh}"
BOOTSTRAP_QMD_SCRIPT="${BOOTSTRAP_QMD_SCRIPT:-$ROOT/scripts/bootstrap/bootstrap_qmd.sh}"
BOOTSTRAP_MEMORY_PLUGIN_SCRIPT="${BOOTSTRAP_MEMORY_PLUGIN_SCRIPT:-$ROOT/scripts/bootstrap/bootstrap_memory_plugin.sh}"
BOOTSTRAP_LOSSLESS_CONTEXT_ENGINE_SCRIPT="${BOOTSTRAP_LOSSLESS_CONTEXT_ENGINE_SCRIPT:-$ROOT/scripts/bootstrap/bootstrap_lossless_context_engine.sh}"
RENDER_OPENCLAW_CONFIG_SCRIPT="${RENDER_OPENCLAW_CONFIG_SCRIPT:-$ROOT/scripts/bootstrap/render_openclaw_config.sh}"
DOCTOR_HOST_SCRIPT="${DOCTOR_HOST_SCRIPT:-$ROOT/scripts/bootstrap/doctor_host.sh}"
INSTALL_HOST_SERVICES_SCRIPT="${INSTALL_HOST_SERVICES_SCRIPT:-$ROOT/scripts/bootstrap/install_host_services.sh}"
CONFIGURE_VARLOCK_ENV_SCRIPT="${CONFIGURE_VARLOCK_ENV_SCRIPT:-$ROOT/scripts/bootstrap/configure_varlock_env.sh}"
CONFIGURE_MODEL_AUTH_SCRIPT="${CONFIGURE_MODEL_AUTH_SCRIPT:-$ROOT/scripts/bootstrap/configure_openclaw_model_auth.sh}"
VERIFY_BASELINE_SCRIPT="${VERIFY_BASELINE_SCRIPT:-$ROOT/scripts/bootstrap/verify_baseline.sh}"
CONFIG_PROFILE=""
ACTIVATE_SERVICES=1
SKIP_BOOTSTRAP=0
FORCE_BOOTSTRAP=0
VERIFY_BASELINE=1
NON_INTERACTIVE=0
AUTO_SKIPPED_BOOTSTRAP=0
declare -a CONFIGURE_VARLOCK_ENV_ARGS=()
declare -a CONFIGURE_MODEL_AUTH_ARGS=()

usage() {
  cat <<'EOF'
Usage: setup.sh [--profile PROFILE] [--skip-bootstrap] [--force-bootstrap] [--no-activate-services] [--no-verify] [--non-interactive]

Guided StrongClaw setup:

1. bootstrap host prerequisites when needed
2. create, normalize, and validate the repo-local Varlock env contract
3. render the selected OpenClaw profile and validate the config
4. configure or verify OpenClaw model/provider auth
5. activate host services
6. run the baseline verification gate

Options:
  --profile PROFILE       Rerender the named config profile after bootstrap.
  --skip-bootstrap        Reuse an already-bootstrapped host and continue from config/env setup.
  --force-bootstrap       Run bootstrap even if StrongClaw already marked the host as ready.
  --no-activate-services  Render host service files without activating them.
  --no-verify             Skip baseline verification.
  --non-interactive       Fail with remediation instead of prompting for missing config.
  -h, --help              Show this help text.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --profile requires a value." >&2
        exit 1
      fi
      CONFIG_PROFILE="$2"
      shift 2
      ;;
    --skip-bootstrap)
      SKIP_BOOTSTRAP=1
      shift
      ;;
    --force-bootstrap)
      FORCE_BOOTSTRAP=1
      shift
      ;;
    --no-activate-services)
      ACTIVATE_SERVICES=0
      shift
      ;;
    --no-verify)
      VERIFY_BASELINE=0
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

if [[ "$SKIP_BOOTSTRAP" -eq 1 && "$FORCE_BOOTSTRAP" -eq 1 ]]; then
  echo "ERROR: --skip-bootstrap and --force-bootstrap cannot be used together." >&2
  exit 1
fi

if [[ -n "$CONFIG_PROFILE" ]]; then
  export OPENCLAW_CONFIG_PROFILE="$CONFIG_PROFILE"
fi
export OPENCLAW_CONFIG_PROFILE="${OPENCLAW_CONFIG_PROFILE:-$STRONGCLAW_DEFAULT_PROFILE}"
if [[ "$NON_INTERACTIVE" -eq 1 ]]; then
  export OPENCLAW_MODEL_SETUP_MODE="${OPENCLAW_MODEL_SETUP_MODE:-env-only}"
  CONFIGURE_VARLOCK_ENV_ARGS+=(--non-interactive)
else
  CONFIGURE_MODEL_AUTH_ARGS+=(--probe)
fi

run_step() {
  local title="$1"
  local remediation="$2"
  shift 2
  echo "== $title =="
  if "$@"; then
    return 0
  fi
  echo "ERROR: $title failed." >&2
  if [[ -n "$remediation" ]]; then
    printf '%s\n' "$remediation" >&2
  fi
  exit 1
}

run_script_step() {
  local title="$1"
  local remediation="$2"
  local script_path="$3"
  shift 3
  run_step "$title" "$remediation" run_shell_entrypoint "$script_path" "$@"
}

describe_bootstrap_mode() {
  if [[ "$FORCE_BOOTSTRAP" -eq 1 ]]; then
    printf 'forced'
    return 0
  fi
  if [[ "$SKIP_BOOTSTRAP" -eq 1 ]]; then
    if [[ "$AUTO_SKIPPED_BOOTSTRAP" -eq 1 ]]; then
      printf 'auto-skipped (host bootstrap already completed)'
      return 0
    fi
    printf 'skipped'
    return 0
  fi
  printf 'enabled'
}

reconcile_profile_assets() {
  if profile_requires_qmd "${OPENCLAW_CONFIG_PROFILE:-$STRONGCLAW_DEFAULT_PROFILE}"; then
    run_script_step \
      "QMD profile assets" \
      "Install the QMD runtime with: $BOOTSTRAP_QMD_SCRIPT" \
      "$BOOTSTRAP_QMD_SCRIPT"
  fi

  if profile_requires_memory_pro_plugin "${OPENCLAW_CONFIG_PROFILE:-$STRONGCLAW_DEFAULT_PROFILE}"; then
    run_script_step \
      "Vendored memory-pro plugin assets" \
      "Install the vendored memory plugin with: $BOOTSTRAP_MEMORY_PLUGIN_SCRIPT" \
      "$BOOTSTRAP_MEMORY_PLUGIN_SCRIPT"
  fi

  if profile_requires_lossless_claw "${OPENCLAW_CONFIG_PROFILE:-$STRONGCLAW_DEFAULT_PROFILE}"; then
    run_script_step \
      "Lossless context assets" \
      "Install the lossless-claw plugin with: $BOOTSTRAP_LOSSLESS_CONTEXT_ENGINE_SCRIPT" \
      "$BOOTSTRAP_LOSSLESS_CONTEXT_ENGINE_SCRIPT"
  fi
}

pause_for_linux_docker_refresh() {
  local runtime_user
  runtime_user="$(setup_state_value "$OPENCLAW_DOCKER_REFRESH_STATE_FILE" RUNTIME_USER)"
  if [[ -z "$runtime_user" ]]; then
    runtime_user="$(id -un)"
  fi
  cat >&2 <<EOF
SETUP PAUSED: Docker access was granted during bootstrap, but this shell has not
picked up the new docker-group membership yet.

Next steps:
- open a fresh login shell as $runtime_user
- rerun: clawops setup

StrongClaw will detect the completed bootstrap automatically and resume from the
remaining setup steps without requiring --skip-bootstrap.
EOF
  exit 1
}

if [[ "$SKIP_BOOTSTRAP" -eq 0 && "$FORCE_BOOTSTRAP" -eq 0 ]] && bootstrap_state_ready; then
  SKIP_BOOTSTRAP=1
  AUTO_SKIPPED_BOOTSTRAP=1
fi

if [[ "$ACTIVATE_SERVICES" -eq 0 && "$VERIFY_BASELINE" -eq 1 ]]; then
  echo "Baseline verification requires active gateway and sidecar services; skipping it because --no-activate-services was selected."
  VERIFY_BASELINE=0
fi

echo "== StrongClaw setup plan =="
printf 'profile: %s\n' "${OPENCLAW_CONFIG_PROFILE:-$STRONGCLAW_DEFAULT_PROFILE}"
printf 'bootstrap: %s\n' "$(describe_bootstrap_mode)"
printf 'activate services: %s\n' "$([[ "$ACTIVATE_SERVICES" -eq 1 ]] && echo yes || echo no)"
printf 'baseline verify: %s\n' "$([[ "$VERIFY_BASELINE" -eq 1 ]] && echo yes || echo no)"
printf 'interactive prompts: %s\n' "$([[ "$NON_INTERACTIVE" -eq 1 ]] && echo disabled || echo enabled)"

if [[ "$SKIP_BOOTSTRAP" -eq 0 || "$FORCE_BOOTSTRAP" -eq 1 ]]; then
  run_script_step \
    "Host bootstrap" \
    "Review the bootstrap output above, fix the missing prerequisite, and rerun: $ROOT/scripts/bootstrap/setup.sh${CONFIG_PROFILE:+ --profile $CONFIG_PROFILE}" \
    "$BOOTSTRAP_SCRIPT"
else
  reconcile_profile_assets
fi

if [[ "${#CONFIGURE_VARLOCK_ENV_ARGS[@]}" -gt 0 ]]; then
  run_script_step \
    "Varlock env contract" \
    "Complete the env contract with: $ROOT/scripts/bootstrap/configure_varlock_env.sh" \
    "$CONFIGURE_VARLOCK_ENV_SCRIPT" "${CONFIGURE_VARLOCK_ENV_ARGS[@]}"
else
  run_script_step \
    "Varlock env contract" \
    "Complete the env contract with: $ROOT/scripts/bootstrap/configure_varlock_env.sh" \
    "$CONFIGURE_VARLOCK_ENV_SCRIPT"
fi

if [[ -n "$CONFIG_PROFILE" || "$SKIP_BOOTSTRAP" -eq 1 ]]; then
  run_script_step \
    "Render OpenClaw config" \
    "Rerender the selected profile with: $ROOT/scripts/bootstrap/render_openclaw_config.sh --profile ${OPENCLAW_CONFIG_PROFILE:-$STRONGCLAW_DEFAULT_PROFILE}" \
    "$RENDER_OPENCLAW_CONFIG_SCRIPT" --profile "${OPENCLAW_CONFIG_PROFILE:-$STRONGCLAW_DEFAULT_PROFILE}"
  run_script_step \
    "OpenClaw config doctor" \
    "Review the rendered config and rerun: $ROOT/scripts/bootstrap/doctor_host.sh" \
    "$DOCTOR_HOST_SCRIPT"
fi

run_script_step \
  "OpenClaw model/provider setup" \
  "Complete provider auth with: $ROOT/scripts/bootstrap/configure_openclaw_model_auth.sh" \
  "$CONFIGURE_MODEL_AUTH_SCRIPT" "${CONFIGURE_MODEL_AUTH_ARGS[@]}"

if [[ "$ACTIVATE_SERVICES" -eq 1 && "$(uname -s)" == "Linux" ]] && docker_shell_refresh_required; then
  if docker_backend_ready; then
    clear_docker_shell_refresh_required
  else
    pause_for_linux_docker_refresh
  fi
fi

if [[ "$ACTIVATE_SERVICES" -eq 1 ]]; then
  run_script_step \
    "Host services" \
    "Render or activate services manually with: $ROOT/scripts/bootstrap/install_host_services.sh --activate" \
    "$INSTALL_HOST_SERVICES_SCRIPT" --activate
else
  run_script_step \
    "Render host services" \
    "Inspect the generated service files, then activate them manually with $ROOT/scripts/bootstrap/install_host_services.sh --activate" \
    "$INSTALL_HOST_SERVICES_SCRIPT"
fi

if [[ "$VERIFY_BASELINE" -eq 1 ]]; then
  run_script_step \
    "Baseline verification" \
    "Review the failing verification step above, fix it, then rerun: $ROOT/scripts/bootstrap/verify_baseline.sh" \
    "$VERIFY_BASELINE_SCRIPT"
fi

cat <<EOF
StrongClaw setup completed.

Next steps:
- Control UI: http://127.0.0.1:18789/
- Deep health scan: clawops doctor
- Manual rerun: $ROOT/scripts/bootstrap/setup.sh${CONFIG_PROFILE:+ --profile $CONFIG_PROFILE}
EOF
