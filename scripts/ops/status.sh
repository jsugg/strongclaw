#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/openclaw.sh"

require_openclaw "Status checks require the OpenClaw CLI."

openclaw gateway status --json
openclaw status --all
