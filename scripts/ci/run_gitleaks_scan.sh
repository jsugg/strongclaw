#!/usr/bin/env bash
set -euo pipefail

gitleaks git --no-banner --no-color --exit-code 1 --log-level warn --redact
