#!/usr/bin/env bash
set -euo pipefail
openclaw gateway status --json
openclaw status --all
