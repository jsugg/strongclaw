#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIGURE_VARLOCK_ENV_SCRIPT="${CONFIGURE_VARLOCK_ENV_SCRIPT:-$ROOT/scripts/bootstrap/configure_varlock_env.sh}"
DOCTOR_HOST_SCRIPT="${DOCTOR_HOST_SCRIPT:-$ROOT/scripts/bootstrap/doctor_host.sh}"
CONFIGURE_MODEL_AUTH_SCRIPT="${CONFIGURE_MODEL_AUTH_SCRIPT:-$ROOT/scripts/bootstrap/configure_openclaw_model_auth.sh}"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/openclaw.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/clawops.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/docker_runtime.sh"

SKIP_RUNTIME=0
PROBE_MODEL_AUTH=1
declare -a MODEL_AUTH_ARGS=(--check-only)

usage() {
  cat <<'EOF'
Usage: doctor_strongclaw.sh [--skip-runtime] [--no-model-probe]

Run a deep StrongClaw readiness scan covering env contract, rendered config,
OpenClaw readiness, model availability, and platform verification.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-runtime)
      SKIP_RUNTIME=1
      shift
      ;;
    --no-model-probe)
      PROBE_MODEL_AUTH=0
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

require_openclaw "StrongClaw doctor requires the OpenClaw CLI."

failures=0

run_check() {
  local title="$1"
  local remediation="$2"
  shift 2
  echo "== $title =="
  if "$@"; then
    return 0
  fi
  failures=$((failures + 1))
  echo "FAILED: $title" >&2
  if [[ -n "$remediation" ]]; then
    printf 'Next step: %s\n' "$remediation" >&2
  fi
  return 0
}

if [[ "$SKIP_RUNTIME" -eq 0 && "$PROBE_MODEL_AUTH" -eq 1 ]]; then
  MODEL_AUTH_ARGS+=(--probe)
fi

run_check \
  "Varlock env contract" \
  "$ROOT/scripts/bootstrap/configure_varlock_env.sh" \
  run_shell_entrypoint "$CONFIGURE_VARLOCK_ENV_SCRIPT" --check-only

run_check \
  "Host toolchain and rendered config" \
  "clawops setup" \
  run_shell_entrypoint "$DOCTOR_HOST_SCRIPT"

if [[ "$SKIP_RUNTIME" -eq 0 && "$(uname -s)" == "Linux" ]] && docker_shell_refresh_required; then
  if docker_backend_ready; then
    clear_docker_shell_refresh_required
  else
    run_check \
      "Linux docker session refresh" \
      "Open a fresh login shell, then rerun clawops setup" \
      false
  fi
fi

run_check \
  "OpenClaw model readiness" \
  "$ROOT/scripts/bootstrap/configure_openclaw_model_auth.sh" \
  run_shell_entrypoint "$CONFIGURE_MODEL_AUTH_SCRIPT" "${MODEL_AUTH_ARGS[@]}"

run_check \
  "OpenClaw doctor" \
  "OPENCLAW_GATEWAY_TOKEN=<token> openclaw doctor --non-interactive" \
  run_openclaw doctor --non-interactive

run_check \
  "OpenClaw security audit" \
  "OPENCLAW_GATEWAY_TOKEN=<token> openclaw security audit --deep" \
  run_openclaw security audit --deep

run_check \
  "OpenClaw secrets audit" \
  "OPENCLAW_GATEWAY_TOKEN=<token> openclaw secrets audit --check" \
  run_openclaw secrets audit --check

if [[ "$SKIP_RUNTIME" -eq 0 ]]; then
  run_check \
    "OpenClaw gateway status" \
    "OPENCLAW_GATEWAY_TOKEN=<token> openclaw gateway status --json" \
    run_openclaw gateway status --json

  run_check \
    "OpenClaw memory status" \
    "OPENCLAW_GATEWAY_TOKEN=<token> openclaw memory status --deep" \
    run_openclaw memory status --deep
fi

verify_platform_suffix=""
if [[ "$SKIP_RUNTIME" -eq 1 ]]; then
  verify_platform_suffix=" --skip-runtime"
fi

if [[ "$SKIP_RUNTIME" -eq 1 ]]; then
  run_check \
    "Platform sidecars" \
    "clawops verify-platform sidecars${verify_platform_suffix}" \
    run_clawops "$ROOT" verify-platform sidecars --skip-runtime

  run_check \
    "Platform observability" \
    "clawops verify-platform observability${verify_platform_suffix}" \
    run_clawops "$ROOT" verify-platform observability --skip-runtime
else
  run_check \
    "Platform sidecars" \
    "clawops verify-platform sidecars" \
    run_clawops "$ROOT" verify-platform sidecars

  run_check \
    "Platform observability" \
    "clawops verify-platform observability" \
    run_clawops "$ROOT" verify-platform observability
fi

run_check \
  "Platform channels" \
  "clawops verify-platform channels" \
  run_clawops "$ROOT" verify-platform channels

if [[ "$failures" -gt 0 ]]; then
  echo "StrongClaw doctor found $failures failing check(s)." >&2
  echo "Run clawops setup or the suggested remediation command, then rerun clawops doctor." >&2
  exit 1
fi

echo "StrongClaw doctor completed successfully."
