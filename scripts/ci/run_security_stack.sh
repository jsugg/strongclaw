#!/usr/bin/env bash
set -euo pipefail

PYTHONPATH=src pytest -q
gitleaks detect --source . --no-git || true
semgrep --config security/semgrep/semgrep.yml . || true
trivy fs --config security/trivy/trivy.yaml . || true
