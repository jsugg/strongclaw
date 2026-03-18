"""Regression tests for operational command surfaces."""

from __future__ import annotations

import pathlib

AUTOMATION_FILES = (
    pathlib.Path("Makefile"),
    pathlib.Path("scripts/bootstrap/verify_baseline.sh"),
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

    assert '"$ROOT/scripts/bootstrap/verify_sidecars.sh" --skip-runtime' in verify_script
    assert '"$ROOT/scripts/bootstrap/verify_observability.sh" --skip-runtime' in verify_script
    assert '"$ROOT/scripts/bootstrap/verify_channels.sh"' in verify_script


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

    assert "clawops verify-platform sidecars" in sidecars
    assert "clawops verify-platform observability" in observability
    assert "clawops verify-platform channels" in channels
    assert "clawops acp-runner" in codex
    assert "clawops acp-runner" in reviewer
    assert fixer_loop.count("clawops acp-runner") == 2


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
    assert 'source "$ROOT/scripts/lib/docker_runtime.sh"' in host
    assert '"$ROOT/scripts/bootstrap/bootstrap_memory_plugin.sh"' in host
    assert '"$ROOT/scripts/bootstrap/render_openclaw_config.sh"' in host
    assert '"$ROOT/scripts/bootstrap/doctor_host.sh"' in host
    assert "acpx@latest" not in host
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


def test_install_script_composes_existing_bootstrap_and_verification_entrypoints() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    install_script = (repo_root / "scripts/bootstrap/install.sh").read_text(encoding="utf-8")

    assert (
        'BOOTSTRAP_SCRIPT="${BOOTSTRAP_SCRIPT:-$ROOT/scripts/bootstrap/bootstrap.sh}"'
        in install_script
    )
    assert (
        'RENDER_OPENCLAW_CONFIG_SCRIPT="${RENDER_OPENCLAW_CONFIG_SCRIPT:-$ROOT/scripts/bootstrap/render_openclaw_config.sh}"'
        in install_script
    )
    assert (
        'DOCTOR_HOST_SCRIPT="${DOCTOR_HOST_SCRIPT:-$ROOT/scripts/bootstrap/doctor_host.sh}"'
        in install_script
    )
    assert (
        'INSTALL_HOST_SERVICES_SCRIPT="${INSTALL_HOST_SERVICES_SCRIPT:-$ROOT/scripts/bootstrap/install_host_services.sh}"'
        in install_script
    )
    assert (
        'VALIDATE_VARLOCK_ENV_SCRIPT="${VALIDATE_VARLOCK_ENV_SCRIPT:-$ROOT/scripts/bootstrap/validate_varlock_env.sh}"'
        in install_script
    )
    assert (
        'VERIFY_BASELINE_SCRIPT="${VERIFY_BASELINE_SCRIPT:-$ROOT/scripts/bootstrap/verify_baseline.sh}"'
        in install_script
    )
    assert "--profile PROFILE" in install_script
    assert "--skip-bootstrap" in install_script
    assert "--no-activate-services" in install_script
    assert "--no-verify" in install_script
    assert '"$VALIDATE_VARLOCK_ENV_SCRIPT"' in install_script
    assert '"$INSTALL_HOST_SERVICES_SCRIPT" --activate' in install_script


def test_ci_environment_sync_exports_the_locked_virtualenv_for_follow_up_steps() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sync_script = (repo_root / "scripts/ci/sync_dev_environment.sh").read_text(encoding="utf-8")

    assert "uv sync --locked --extra dev" in sync_script
    assert 'printf \'%s\\n\' "$ROOT/.venv/bin" >>"$GITHUB_PATH"' in sync_script
    assert "printf 'VIRTUAL_ENV=%s\\n' \"$ROOT/.venv\"" in sync_script


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
    assert "./scripts/bootstrap/install.sh" in setup_guide
    assert "make install" in quickstart
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
