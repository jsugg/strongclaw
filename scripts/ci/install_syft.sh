#!/usr/bin/env bash
set -euo pipefail

SYFT_VERSION="${SYFT_VERSION:-v1.42.2}"
archive="syft_${SYFT_VERSION#v}_linux_amd64.tar.gz"

curl -fsSL "https://github.com/anchore/syft/releases/download/${SYFT_VERSION}/${archive}" -o "${RUNNER_TEMP}/${archive}"
tar -xzf "${RUNNER_TEMP}/${archive}" -C "${RUNNER_TEMP}" syft
install "${RUNNER_TEMP}/syft" "${HOME}/.local/bin/syft"
echo "${HOME}/.local/bin" >> "${GITHUB_PATH}"
