#!/usr/bin/env bash
set -euo pipefail

GITLEAKS_VERSION="${GITLEAKS_VERSION:-8.28.0}"
archive="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"

curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/${archive}" -o "${RUNNER_TEMP}/${archive}"
mkdir -p "${HOME}/.local/bin"
tar -xzf "${RUNNER_TEMP}/${archive}" -C "${RUNNER_TEMP}" gitleaks
install "${RUNNER_TEMP}/gitleaks" "${HOME}/.local/bin/gitleaks"
echo "${HOME}/.local/bin" >> "${GITHUB_PATH}"
