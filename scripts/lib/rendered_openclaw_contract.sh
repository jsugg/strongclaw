#!/usr/bin/env bash

set -euo pipefail

rendered_openclaw_value() {
  local config_path="$1"
  local query="$2"
  jq -r "$query // \"\"" "$config_path"
}

rendered_openclaw_memory_backend() {
  local config_path="$1"
  rendered_openclaw_value "$config_path" '.memory.backend'
}

rendered_openclaw_memory_slot() {
  local config_path="$1"
  rendered_openclaw_value "$config_path" '.plugins.slots.memory'
}

rendered_openclaw_context_engine_slot() {
  local config_path="$1"
  rendered_openclaw_value "$config_path" '.plugins.slots.contextEngine'
}

rendered_openclaw_hypermemory_config_path() {
  local config_path="$1"
  rendered_openclaw_value \
    "$config_path" \
    '.plugins.entries["strongclaw-hypermemory"].config.configPath'
}

rendered_openclaw_lossless_plugin_path() {
  local config_path="$1"
  jq -r '.plugins.load.paths[]? | select(test("lossless-claw"))' "$config_path" | head -n 1
}

rendered_openclaw_uses_qmd() {
  local config_path="$1"
  [[ "$(rendered_openclaw_memory_backend "$config_path")" == "qmd" ]]
}

rendered_openclaw_uses_lossless_claw() {
  local config_path="$1"
  [[ "$(rendered_openclaw_context_engine_slot "$config_path")" == "lossless-claw" ]]
}

rendered_openclaw_uses_hypermemory() {
  local config_path="$1"
  [[ "$(rendered_openclaw_memory_slot "$config_path")" == "strongclaw-hypermemory" ]]
}
