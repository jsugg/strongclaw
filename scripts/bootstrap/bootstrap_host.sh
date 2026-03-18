#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
case "$(uname -s)" in
  Darwin)
    exec "$ROOT/scripts/bootstrap/bootstrap_macos.sh" "$@"
    ;;
  Linux)
    exec "$ROOT/scripts/bootstrap/bootstrap_linux.sh" "$@"
    ;;
  *)
    echo "unsupported host OS for bootstrap: $(uname -s)" >&2
    exit 1
    ;;
esac
