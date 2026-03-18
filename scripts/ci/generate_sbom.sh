#!/usr/bin/env bash
set -euo pipefail

OUTPUT_PATH="${1:-sbom.spdx.json}"

syft dir:. -o "spdx-json=${OUTPUT_PATH}"
