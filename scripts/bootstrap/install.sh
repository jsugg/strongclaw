#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BOOTSTRAP_SCRIPT="${BOOTSTRAP_SCRIPT:-$ROOT/scripts/bootstrap/bootstrap.sh}"
RENDER_OPENCLAW_CONFIG_SCRIPT="${RENDER_OPENCLAW_CONFIG_SCRIPT:-$ROOT/scripts/bootstrap/render_openclaw_config.sh}"
DOCTOR_HOST_SCRIPT="${DOCTOR_HOST_SCRIPT:-$ROOT/scripts/bootstrap/doctor_host.sh}"
INSTALL_HOST_SERVICES_SCRIPT="${INSTALL_HOST_SERVICES_SCRIPT:-$ROOT/scripts/bootstrap/install_host_services.sh}"
VALIDATE_VARLOCK_ENV_SCRIPT="${VALIDATE_VARLOCK_ENV_SCRIPT:-$ROOT/scripts/bootstrap/validate_varlock_env.sh}"
VERIFY_BASELINE_SCRIPT="${VERIFY_BASELINE_SCRIPT:-$ROOT/scripts/bootstrap/verify_baseline.sh}"
CONFIG_PROFILE=""
ACTIVATE_SERVICES=1
SKIP_BOOTSTRAP=0
VERIFY_BASELINE=1

usage() {
  cat <<'EOF'
Usage: install.sh [--profile PROFILE] [--skip-bootstrap] [--no-activate-services] [--no-verify]

Bootstrap the host, optionally rerender a named OpenClaw config profile,
activate the repo-local gateway and sidecar services, and verify the baseline.

Options:
  --profile PROFILE       Rerender the named config profile after bootstrap.
  --skip-bootstrap        Reuse an already-bootstrapped host and continue from env validation.
  --no-activate-services  Render host service files without activating them.
  --no-verify             Skip baseline verification.
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
    --no-activate-services)
      ACTIVATE_SERVICES=0
      shift
      ;;
    --no-verify)
      VERIFY_BASELINE=0
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

if [[ -n "$CONFIG_PROFILE" ]]; then
  export OPENCLAW_CONFIG_PROFILE="$CONFIG_PROFILE"
fi

if [[ "$SKIP_BOOTSTRAP" -eq 0 ]]; then
  echo "== Host bootstrap =="
  "$BOOTSTRAP_SCRIPT"
fi

if [[ -n "$CONFIG_PROFILE" ]]; then
  echo "== Rerender OpenClaw config profile: $CONFIG_PROFILE =="
  "$RENDER_OPENCLAW_CONFIG_SCRIPT" --profile "$CONFIG_PROFILE"
  "$DOCTOR_HOST_SCRIPT"
fi

echo "== Host services =="
if [[ "$ACTIVATE_SERVICES" -eq 1 ]]; then
  echo "== Varlock env contract =="
  "$VALIDATE_VARLOCK_ENV_SCRIPT"
  "$INSTALL_HOST_SERVICES_SCRIPT" --activate
else
  "$INSTALL_HOST_SERVICES_SCRIPT"
fi

if [[ "$VERIFY_BASELINE" -eq 1 ]]; then
  echo "== Baseline verification =="
  "$VERIFY_BASELINE_SCRIPT"
fi
