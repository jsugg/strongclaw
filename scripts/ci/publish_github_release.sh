#!/usr/bin/env bash
set -euo pipefail

tag_name="${1:?tag name required}"
shift

if gh release view "$tag_name" >/dev/null 2>&1; then
  gh release upload "$tag_name" "$@" --clobber
else
  gh release create "$tag_name" "$@" --verify-tag --generate-notes
fi
