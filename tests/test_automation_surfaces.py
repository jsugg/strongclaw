"""Regression tests for operational command surfaces."""

from __future__ import annotations

import pathlib

AUTOMATION_FILES = (
    pathlib.Path("Makefile"),
    pathlib.Path("scripts/bootstrap/verify_baseline.sh"),
    pathlib.Path(".github/workflows/compatibility-matrix.yml"),
    pathlib.Path(".github/workflows/harness.yml"),
    pathlib.Path("scripts/bootstrap/run_harness_smoke.sh"),
)

COMPOSE_FILES = (
    pathlib.Path("platform/compose/docker-compose.aux-stack.yaml"),
    pathlib.Path("platform/compose/docker-compose.browser-lab.yaml"),
    pathlib.Path("platform/compose/docker-compose.langfuse.optional.yaml"),
)


def test_automation_surfaces_do_not_use_obsolete_harness_subcommand() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    for relative_path in AUTOMATION_FILES:
        text = (repo_root / relative_path).read_text(encoding="utf-8")
        assert "clawops harness run" not in text, f"obsolete harness CLI in {relative_path}"


def test_verify_baseline_runs_platform_static_proof() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    verify_script = (repo_root / "scripts/bootstrap/verify_baseline.sh").read_text(encoding="utf-8")

    assert (
        'VERIFY_OPENCLAW_MODELS_SCRIPT="${VERIFY_OPENCLAW_MODELS_SCRIPT:-$ROOT/scripts/bootstrap/configure_openclaw_model_auth.sh}"'
        in verify_script
    )
    assert '"$VERIFY_OPENCLAW_MODELS_SCRIPT" --check-only' in verify_script
    assert (
        'VERIFY_HYPERMEMORY_SCRIPT="${VERIFY_HYPERMEMORY_SCRIPT:-$ROOT/scripts/bootstrap/verify_hypermemory.sh}"'
        in verify_script
    )
    assert '"$ROOT/scripts/bootstrap/verify_sidecars.sh" --skip-runtime' in verify_script
    assert '"$ROOT/scripts/bootstrap/verify_observability.sh" --skip-runtime' in verify_script
    assert '"$ROOT/scripts/bootstrap/verify_channels.sh"' in verify_script
    assert 'uv run --project "$ROOT" --locked --extra dev pytest -q "$ROOT/tests"' in verify_script
    assert 'pytest -q "$ROOT/tests"' not in verify_script.replace(
        'uv run --project "$ROOT" --locked --extra dev pytest -q "$ROOT/tests"',
        "",
    )


def test_current_state_tracks_hypermemory_and_plan2_completion() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    plugin = (repo_root / "platform/plugins/strongclaw-hypermemory/index.js").read_text(
        encoding="utf-8"
    )
    openclaw_config = (repo_root / "src/clawops/openclaw_config.py").read_text(encoding="utf-8")
    models = (repo_root / "src/clawops/hypermemory/models.py").read_text(encoding="utf-8")

    assert (repo_root / "src/clawops/hypermemory/providers.py").exists()
    assert "before_prompt_build" in plugin
    assert "77-hypermemory.example.json5" in openclaw_config
    assert "qdrant_dense_hybrid" in models
    assert "qdrant_sparse_dense_hybrid" in models


def test_dev_sidecar_scripts_make_repo_local_state_explicit() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    app_paths = (repo_root / "scripts/lib/app_paths.sh").read_text(encoding="utf-8")
    launch_dev = (repo_root / "scripts/ops/launch_sidecars_dev.sh").read_text(encoding="utf-8")
    stop_dev = (repo_root / "scripts/ops/stop_sidecars_dev.sh").read_text(encoding="utf-8")
    reset_dev = (repo_root / "scripts/ops/reset_dev_compose_state.sh").read_text(encoding="utf-8")
    prune_qdrant = (repo_root / "scripts/ops/prune_qdrant_test_collections.sh").read_text(
        encoding="utf-8"
    )

    assert "strongclaw_repo_local_compose_state_dir" in app_paths
    assert 'export_strongclaw_repo_local_compose_state_dir "$ROOT"' in launch_dev
    assert 'export_strongclaw_repo_local_compose_state_dir "$ROOT"' in stop_dev
    assert 'STATE_DIR="$(strongclaw_repo_local_compose_state_dir "$ROOT")"' in reset_dev
    assert 'declare -a PREFIXES=("memory-v2-int-")' in prune_qdrant


def test_litellm_sidecar_receives_dynamic_embedding_and_provider_env() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    compose = (repo_root / "platform/compose/docker-compose.aux-stack.yaml").read_text(
        encoding="utf-8"
    )

    assert "HYPERMEMORY_EMBEDDING_MODEL" in compose
    assert "HYPERMEMORY_EMBEDDING_API_BASE" in compose
    assert "OPENAI_API_KEY" in compose
    assert "ANTHROPIC_API_KEY" in compose
    assert "OPENROUTER_API_KEY" in compose
    assert "ZAI_API_KEY" in compose
    assert "MOONSHOT_API_KEY" in compose
    assert "OLLAMA_API_KEY" in compose
    assert "host.docker.internal:host-gateway" in compose


def test_security_harness_smoke_uses_uv_managed_python() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    suite = (repo_root / "platform/configs/harness/security_regressions.yaml").read_text(
        encoding="utf-8"
    )

    assert (
        '["uv", "run", "--project", ".", "python", "-m", "clawops", "merge-json", "--help"]'
        in suite
    )
    assert (
        '["uv", "run", "--project", ".", "python", "-m", "clawops", "context", "--help"]' in suite
    )
    assert '["python3", "-m", "clawops"' not in suite


def test_platform_verification_and_acp_scripts_use_shared_clawops_entrypoints() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]

    sidecars = (repo_root / "scripts/bootstrap/verify_sidecars.sh").read_text(encoding="utf-8")
    observability = (repo_root / "scripts/bootstrap/verify_observability.sh").read_text(
        encoding="utf-8"
    )
    channels = (repo_root / "scripts/bootstrap/verify_channels.sh").read_text(encoding="utf-8")
    codex = (repo_root / "scripts/workers/run_codex_session.sh").read_text(encoding="utf-8")
    reviewer = (repo_root / "scripts/workers/run_claude_review.sh").read_text(encoding="utf-8")
    fixer_loop = (repo_root / "scripts/workers/reviewer_fixer_loop.sh").read_text(encoding="utf-8")
    harness_smoke = (repo_root / "scripts/bootstrap/run_harness_smoke.sh").read_text(
        encoding="utf-8"
    )

    assert 'resolve_clawops_bin "$ROOT"' in sidecars
    assert "verify-platform sidecars" in sidecars
    assert 'resolve_clawops_bin "$ROOT"' in observability
    assert "verify-platform observability" in observability
    assert 'resolve_clawops_bin "$ROOT"' in channels
    assert "verify-platform channels" in channels
    assert 'resolve_clawops_bin "$ROOT"' in codex
    assert "acp-runner" in codex
    assert 'resolve_clawops_bin "$ROOT"' in reviewer
    assert "acp-runner" in reviewer
    assert fixer_loop.count('run_clawops "$ROOT" acp-runner') == 2
    assert harness_smoke.count('run_clawops "$ROOT" harness') == 2


def test_github_workflows_pin_actions_to_full_commit_shas() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]

    for workflow_path in (repo_root / ".github/workflows").glob("*.yml"):
        text = workflow_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- uses: "):
                continue
            assert (
                "@master" not in stripped
            ), f"mutable action ref in {workflow_path.name}: {stripped}"
            action_ref = stripped.removeprefix("- uses: ")
            owner_and_repo, _, version = action_ref.partition("@")
            assert (
                owner_and_repo and version
            ), f"invalid action ref in {workflow_path.name}: {stripped}"
            assert (
                len(version.split("#", 1)[0].strip()) == 40
            ), f"workflow action must pin a full commit SHA in {workflow_path.name}: {stripped}"


def test_bootstrap_script_keeps_core_host_setup_contract() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    host = (repo_root / "scripts/bootstrap/bootstrap.sh").read_text(encoding="utf-8")

    assert 'HOST_OS="$(uname -s)"' in host
    assert 'case "$HOST_OS" in' in host
    assert '"$ROOT/scripts/bootstrap/preflight.sh"' in host
    assert "ensure_docker_compatible_runtime darwin" in host
    assert "ensure_docker_compatible_runtime linux" in host
    assert 'UV_VERSION="${UV_VERSION:-0.10.9}"' in host
    assert 'VARLOCK_VERSION="${VARLOCK_VERSION:-0.5.0}"' in host
    assert "ensure_uv" in host
    assert "https://astral.sh/uv/${UV_VERSION}/install.sh" in host
    assert 'ensure_varlock_installed "$VARLOCK_VERSION"' in host
    assert 'uv sync --project "$ROOT" --locked --extra dev' in host
    assert (
        'mkdir -p "$(strongclaw_data_dir)" "$(strongclaw_state_dir)" "$(strongclaw_log_dir)" "$(strongclaw_compose_state_dir)"'
        in host
    )
    assert 'source "$ROOT/scripts/lib/docker_runtime.sh"' in host
    assert 'source "$ROOT/scripts/lib/app_paths.sh"' in host
    assert 'source "$ROOT/scripts/lib/varlock.sh"' in host
    assert (
        'BOOTSTRAP_MEMORY_PLUGIN_SCRIPT="${BOOTSTRAP_MEMORY_PLUGIN_SCRIPT:-$ROOT/scripts/bootstrap/bootstrap_memory_plugin.sh}"'
        in host
    )
    assert 'if profile_requires_lossless_claw "$OPENCLAW_CONFIG_PROFILE"; then' in host
    assert (
        'BOOTSTRAP_LOSSLESS_CONTEXT_ENGINE_SCRIPT="${BOOTSTRAP_LOSSLESS_CONTEXT_ENGINE_SCRIPT:-$ROOT/scripts/bootstrap/bootstrap_lossless_context_engine.sh}"'
        in host
    )
    assert "ensure_command_or_brew bun bun" not in host
    assert '"$ROOT/scripts/bootstrap/render_openclaw_config.sh"' in host
    assert '"$ROOT/scripts/bootstrap/doctor_host.sh"' in host
    assert "acpx@latest" not in host
    assert "pip install -e" not in host
    assert "|| true" not in host


def test_service_installers_render_host_specific_service_templates() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    runtime_user = (repo_root / "scripts/bootstrap/create_openclawsvc.sh").read_text(
        encoding="utf-8"
    )
    host = (repo_root / "scripts/bootstrap/install_host_services.sh").read_text(encoding="utf-8")
    gateway_unit = (repo_root / "platform/systemd/openclaw-gateway.service").read_text(
        encoding="utf-8"
    )
    sidecars_unit = (repo_root / "platform/systemd/openclaw-sidecars.service").read_text(
        encoding="utf-8"
    )

    assert 'HOST_OS="$(uname -s)"' in runtime_user
    assert 'sudo sysadminctl -addUser "$USERNAME" -admin NO' in runtime_user
    assert 'loginctl enable-linger "$USERNAME"' in runtime_user
    assert 'case "$(uname -s)" in' in host
    assert 'LAUNCHD_DIR="${LAUNCHD_DIR:-$HOME/Library/LaunchAgents}"' in host
    assert 'SYSTEMD_DIR="${SYSTEMD_DIR:-$HOME/.config/systemd/user}"' in host
    assert "Usage: install_host_services.sh [--activate]" in host
    assert (
        'echo "Run: launchctl bootstrap gui/$(id -u) $LAUNCHD_DIR/ai.openclaw.gateway.plist"'
        in host
    )
    assert 'echo "Run: systemctl --user enable --now openclaw-gateway.service"' in host
    assert "WorkingDirectory=__REPO_ROOT__" in gateway_unit
    assert (
        "ExecStart=/bin/bash -lc '__REPO_ROOT__/scripts/ops/launch_gateway_with_varlock.sh'"
        in gateway_unit
    )
    assert (
        "ExecStart=/bin/bash -lc '__REPO_ROOT__/scripts/ops/launch_sidecars_with_varlock.sh'"
        in sidecars_unit
    )


def test_setup_script_composes_existing_bootstrap_and_verification_entrypoints() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    setup_script = (repo_root / "scripts/bootstrap/setup.sh").read_text(encoding="utf-8")

    assert (
        'BOOTSTRAP_SCRIPT="${BOOTSTRAP_SCRIPT:-$ROOT/scripts/bootstrap/bootstrap.sh}"'
        in setup_script
    )
    assert (
        'RENDER_OPENCLAW_CONFIG_SCRIPT="${RENDER_OPENCLAW_CONFIG_SCRIPT:-$ROOT/scripts/bootstrap/render_openclaw_config.sh}"'
        in setup_script
    )
    assert (
        'DOCTOR_HOST_SCRIPT="${DOCTOR_HOST_SCRIPT:-$ROOT/scripts/bootstrap/doctor_host.sh}"'
        in setup_script
    )
    assert (
        'INSTALL_HOST_SERVICES_SCRIPT="${INSTALL_HOST_SERVICES_SCRIPT:-$ROOT/scripts/bootstrap/install_host_services.sh}"'
        in setup_script
    )
    assert (
        'CONFIGURE_VARLOCK_ENV_SCRIPT="${CONFIGURE_VARLOCK_ENV_SCRIPT:-$ROOT/scripts/bootstrap/configure_varlock_env.sh}"'
        in setup_script
    )
    assert (
        'CONFIGURE_MODEL_AUTH_SCRIPT="${CONFIGURE_MODEL_AUTH_SCRIPT:-$ROOT/scripts/bootstrap/configure_openclaw_model_auth.sh}"'
        in setup_script
    )
    assert (
        'VERIFY_BASELINE_SCRIPT="${VERIFY_BASELINE_SCRIPT:-$ROOT/scripts/bootstrap/verify_baseline.sh}"'
        in setup_script
    )
    assert "--profile PROFILE" in setup_script
    assert "--skip-bootstrap" in setup_script
    assert "--no-activate-services" in setup_script
    assert "--no-verify" in setup_script
    assert "--non-interactive" in setup_script
    assert "run_step" in setup_script
    assert '"$CONFIGURE_VARLOCK_ENV_SCRIPT"' in setup_script
    assert '"$CONFIGURE_MODEL_AUTH_SCRIPT" "${CONFIGURE_MODEL_AUTH_ARGS[@]}"' in setup_script
    assert '"$INSTALL_HOST_SERVICES_SCRIPT" --activate' in setup_script


def test_make_install_uses_uv_managed_environment() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")

    assert "$(UV) sync $(DEV_SYNC_FLAGS)" in makefile
    assert "clawops setup" in makefile
    assert "clawops doctor" in makefile
    assert "$(PIP) install -e ." not in makefile


def test_ci_environment_sync_exports_the_locked_virtualenv_for_follow_up_steps() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sync_script = (repo_root / "scripts/ci/sync_dev_environment.sh").read_text(encoding="utf-8")

    assert "uv sync --locked --extra dev" in sync_script
    assert 'printf \'%s\\n\' "$ROOT/.venv/bin" >>"$GITHUB_PATH"' in sync_script
    assert "printf 'VIRTUAL_ENV=%s\\n' \"$ROOT/.venv\"" in sync_script


def test_ci_validation_scripts_force_safe_wrapper_retry_mode() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    quality_gate = (repo_root / "scripts/ci/run_repository_quality_gate.sh").read_text(
        encoding="utf-8"
    )
    nightly = (repo_root / "scripts/ci/run_nightly_validation.sh").read_text(encoding="utf-8")

    assert ': "${CLAWOPS_HTTP_RETRY_MODE:=safe}"' in quality_gate
    assert "export CLAWOPS_HTTP_RETRY_MODE" in quality_gate
    assert ': "${CLAWOPS_HTTP_RETRY_MODE:=safe}"' in nightly
    assert "export CLAWOPS_HTTP_RETRY_MODE" in nightly


def test_ci_compatibility_matrix_tracks_supported_python_and_node_versions() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    compatibility = (repo_root / ".github/workflows/compatibility-matrix.yml").read_text(
        encoding="utf-8"
    )
    memory_plugin = (repo_root / ".github/workflows/memory-plugin-verification.yml").read_text(
        encoding="utf-8"
    )
    harness = (repo_root / ".github/workflows/harness.yml").read_text(encoding="utf-8")
    nightly = (repo_root / ".github/workflows/nightly.yml").read_text(encoding="utf-8")

    assert 'python-version: ["3.12", "3.13"]' in compatibility
    assert 'node-version: ["22.16.0", "24.13.1"]' in compatibility
    assert "./scripts/ci/run_setup_compatibility_smoke.sh" in compatibility
    assert 'node-version: ["22.16.0", "24.13.1"]' in memory_plugin
    assert "${{ runner.temp }}/strongclaw/harness" in harness
    assert "${{ runner.temp }}/strongclaw/nightly" in nightly


def test_setup_guide_uses_profile_renderer_for_placeholder_backed_acp_overlay() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    setup_guide = (repo_root / "SETUP_GUIDE.md").read_text(encoding="utf-8")
    usage_guide = (repo_root / "USAGE_GUIDE.md").read_text(encoding="utf-8")

    assert "./scripts/bootstrap/render_openclaw_config.sh --profile acp" in setup_guide
    assert "clawops merge-json \\\n  --base ~/.openclaw/openclaw.json" not in setup_guide
    assert "clawops render-openclaw-config" in usage_guide


def test_top_level_docs_use_current_repo_identity_and_search_examples() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    quickstart = (repo_root / "QUICKSTART.md").read_text(encoding="utf-8")
    setup_guide = (repo_root / "SETUP_GUIDE.md").read_text(encoding="utf-8")
    usage_guide = (repo_root / "USAGE_GUIDE.md").read_text(encoding="utf-8")

    assert "# Strongclaw / ClawOps" in readme
    assert "openclaw-platform-bootstrap" not in readme
    assert "openclaw-platform-bootstrap" not in setup_guide
    assert "best-effort install" not in quickstart
    assert "./scripts/bootstrap/doctor_host.sh" in quickstart
    assert "./scripts/bootstrap/preflight.sh" not in setup_guide
    assert "clawops setup" in setup_guide
    assert "make install" in quickstart
    assert "installs `uv`" in quickstart
    assert "installs `uv`" in setup_guide
    assert 'openclaw memory search --query "ClawOps" --max-results 1' in quickstart
    assert 'openclaw memory search --query "ClawOps" --max-results 1' in usage_guide


def test_compose_images_are_pinned_to_content_digests() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]

    for compose_path in COMPOSE_FILES:
        text = (repo_root / compose_path).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("image: "):
                continue
            assert "@sha256:" in stripped, f"compose image must pin a digest in {compose_path}"


def test_acpx_worker_readme_matches_reviewed_install_version() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    readme = (repo_root / "platform/workers/acpx/README.md").read_text(encoding="utf-8")

    assert "npm install -g acpx@0.3.0" in readme
    assert "acpx@latest" not in readme
