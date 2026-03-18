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


def test_local_automation_reuses_shared_harness_smoke_script() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    verify_script = (repo_root / "scripts/bootstrap/verify_baseline.sh").read_text(encoding="utf-8")
    workflow = (repo_root / ".github/workflows/harness.yml").read_text(encoding="utf-8")

    assert "RUNS_DIR ?= ./.runs" in makefile
    assert "./scripts/bootstrap/run_harness_smoke.sh $(RUNS_DIR)" in makefile
    assert '"$ROOT/scripts/bootstrap/run_harness_smoke.sh" "$ROOT/.runs"' in verify_script
    assert "./scripts/bootstrap/run_harness_smoke.sh ./.runs" in workflow


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


def test_security_workflow_includes_plugin_path_for_codeql_javascript_scan() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]

    workflow = (repo_root / ".github/workflows/security.yml").read_text(encoding="utf-8")
    codeql_config = (repo_root / "security/codeql/codeql-config.yml").read_text(encoding="utf-8")

    assert "actions: read" in workflow
    assert "contents: read" in workflow
    assert "actions/checkout@93cb6efe18208431cddfb8368fd83d5badbf9bfd" in workflow
    assert "languages: python,javascript" in workflow
    assert "actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405" in workflow
    assert "github/codeql-action/init@b1bff81932f5cdfc8695c7752dcee935dcd061c8" in workflow
    assert "github/codeql-action/analyze@b1bff81932f5cdfc8695c7752dcee935dcd061c8" in workflow
    assert 'GITLEAKS_VERSION: "8.28.0"' in workflow
    assert "gitleaks git --no-banner --no-color --exit-code 1 --log-level warn --redact" in workflow
    assert "--cov=src/clawops" in workflow
    assert 'SYFT_VERSION: "v1.42.2"' in workflow
    assert "syft dir:. -o spdx-json=sbom.spdx.json" in workflow
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow
    assert "actions/setup-node@v6" not in workflow
    assert "cache: false" in workflow
    assert "pull-requests: write" in workflow
    assert "security-events: write" in workflow
    assert "aquasecurity/trivy-action@57a97c7e7821a5776cebc9bb87c984fa69cba8f1" in workflow
    assert "  - platform/plugins" in codeql_config
    assert "  - platform/plugins/memory-lancedb-pro" in codeql_config


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


def test_bootstrap_scripts_fail_fast_pin_acpx_and_render_openclaw_config() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    host = (repo_root / "scripts/bootstrap/bootstrap_host.sh").read_text(encoding="utf-8")
    preflight = (repo_root / "scripts/bootstrap/preflight_host.sh").read_text(encoding="utf-8")
    doctor = (repo_root / "scripts/bootstrap/doctor_host.sh").read_text(encoding="utf-8")
    memory_plugin = (repo_root / "scripts/bootstrap/bootstrap_memory_plugin.sh").read_text(
        encoding="utf-8"
    )
    macos = (repo_root / "scripts/bootstrap/bootstrap_macos.sh").read_text(encoding="utf-8")
    linux = (repo_root / "scripts/bootstrap/bootstrap_linux.sh").read_text(encoding="utf-8")

    assert 'case "$(uname -s)" in' in host
    assert 'exec "$ROOT/scripts/bootstrap/bootstrap_macos.sh" "$@"' in host
    assert 'exec "$ROOT/scripts/bootstrap/bootstrap_linux.sh" "$@"' in host
    assert 'case "$(uname -s)" in' in preflight
    assert 'exec "$ROOT/scripts/bootstrap/preflight_macos.sh" "$@"' in preflight
    assert 'exec "$ROOT/scripts/bootstrap/preflight_linux.sh" "$@"' in preflight
    assert 'OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-$HOME/.openclaw/openclaw.json}"' in doctor
    assert "openclaw --version" in doctor
    assert "openclaw config validate" in doctor
    assert "acpx --version" in doctor
    assert "memory_plugin_lancedb_version" in memory_plugin
    assert "@lancedb/lancedb@$RESOLVED_LANCEDB_VERSION" in memory_plugin
    for script in (macos, linux):
        assert 'ACPX_VERSION="${ACPX_VERSION:-0.3.0}"' in script
        assert '"$ROOT/scripts/bootstrap/bootstrap_memory_plugin.sh"' in script
        assert '"$ROOT/scripts/bootstrap/render_openclaw_config.sh"' in script
        assert '"$ROOT/scripts/bootstrap/doctor_host.sh"' in script
        assert "acpx@latest" not in script
    assert '"$ROOT/scripts/bootstrap/preflight_macos.sh"' in macos
    assert '"$ROOT/scripts/bootstrap/preflight_linux.sh"' in linux
    assert "|| true" not in macos


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
    assert "./scripts/bootstrap/preflight_host.sh" in setup_guide
    assert 'openclaw memory search --query "ClawOps" --max-results 1' in quickstart
    assert 'openclaw memory search --query "ClawOps" --max-results 1' in usage_guide
    assert "for either a macOS or Linux operator host" in setup_guide
    assert "platform/docs/HOST_PLATFORMS.md" in readme
    assert "./scripts/bootstrap/create_openclawsvc.sh" in setup_guide


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
