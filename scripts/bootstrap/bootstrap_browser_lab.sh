#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/app_paths.sh"
export_strongclaw_compose_state_dir
docker compose -f "$ROOT/platform/compose/docker-compose.browser-lab.yaml" up -d
"$ROOT/scripts/ops/check_loopback_bindings.sh" 3128 9222
cat <<'EOF'
Browser lab started.

Run ./scripts/workers/run_browser_lab_exfil_tests.sh next.
EOF
