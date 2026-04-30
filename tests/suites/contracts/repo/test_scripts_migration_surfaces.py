"""Regression checks for the Python-native scripts migration surfaces."""

from __future__ import annotations

from tests.utils.helpers.repo import REPO_ROOT


def test_makefile_uses_python_native_operational_targets() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "preferred_python.sh" not in makefile
    assert "scripts/ops/" not in makefile
    assert "PYTHONPATH=src" not in makefile
    assert "--extra dev" not in makefile
    assert "$(UV) run --locked pytest" in makefile
    assert "dev-shell: install" in makefile
    assert "$(RUN) clawops ops sidecars up" in makefile
    assert "$(RUN) clawops baseline verify" in makefile
    assert "$(RUN) clawops recovery backup-create" in makefile
    assert "./bin/clawops-dev render-openclaw-config" in makefile


def test_repo_dev_entrypoints_enable_isolated_dev_runtime_mode() -> None:
    dev_env = (REPO_ROOT / "scripts" / "dev-env.sh").read_text(encoding="utf-8")
    wrapper = (REPO_ROOT / "bin" / "clawops-dev").read_text(encoding="utf-8")

    assert 'runtime_root="${STRONGCLAW_RUNTIME_ROOT:-$repo_root/.local/dev-runtime}"' in dev_env
    assert 'export STRONGCLAW_ASSET_ROOT="${STRONGCLAW_ASSET_ROOT:-$repo_root}"' in dev_env
    assert 'export STRONGCLAW_RUNTIME_ROOT="$runtime_root"' in dev_env
    assert 'export OPENCLAW_PROFILE="${OPENCLAW_PROFILE:-strongclaw-dev}"' in dev_env
    assert (
        'export OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-$OPENCLAW_STATE_DIR/openclaw.json}"'
        in dev_env
    )
    assert 'export OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-$OPENCLAW_CONFIG_PATH}"' in dev_env
    assert 'PATH="$repo_root/bin:$PATH"' in dev_env
    assert '. "$venv_activate"' in dev_env
    assert 'runtime_root="${STRONGCLAW_RUNTIME_ROOT:-$repo_root/.local/dev-runtime}"' in wrapper
    assert 'export STRONGCLAW_ASSET_ROOT="${STRONGCLAW_ASSET_ROOT:-$repo_root}"' in wrapper
    assert 'export STRONGCLAW_RUNTIME_ROOT="$runtime_root"' in wrapper
    assert 'export OPENCLAW_PROFILE="${OPENCLAW_PROFILE:-strongclaw-dev}"' in wrapper
    assert (
        'export OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-$OPENCLAW_STATE_DIR/openclaw.json}"'
        in wrapper
    )
    assert 'export OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-$OPENCLAW_CONFIG_PATH}"' in wrapper
    assert 'exec uv run --project "$repo_root" clawops "$@"' in wrapper


def test_service_templates_call_repo_venv_python() -> None:
    gateway = (REPO_ROOT / "platform/systemd/openclaw-gateway.service").read_text(encoding="utf-8")
    browserlab = (REPO_ROOT / "platform/systemd/openclaw-browserlab.service").read_text(
        encoding="utf-8"
    )
    maintenance_service = (REPO_ROOT / "platform/systemd/openclaw-maintenance.service").read_text(
        encoding="utf-8"
    )
    backup_create_service = (
        REPO_ROOT / "platform/systemd/openclaw-backup-create.service"
    ).read_text(encoding="utf-8")
    backup_verify_service = (
        REPO_ROOT / "platform/systemd/openclaw-backup-verify.service"
    ).read_text(encoding="utf-8")
    maintenance_timer = (REPO_ROOT / "platform/systemd/openclaw-maintenance.timer").read_text(
        encoding="utf-8"
    )
    backup_create_timer = (REPO_ROOT / "platform/systemd/openclaw-backup-create.timer").read_text(
        encoding="utf-8"
    )
    backup_verify_timer = (REPO_ROOT / "platform/systemd/openclaw-backup-verify.timer").read_text(
        encoding="utf-8"
    )
    sidecars = (REPO_ROOT / "platform/systemd/openclaw-sidecars.service").read_text(
        encoding="utf-8"
    )
    launchd_gateway = (REPO_ROOT / "platform/launchd/ai.openclaw.gateway.plist.template").read_text(
        encoding="utf-8"
    )
    launchd_sidecars = (
        REPO_ROOT / "platform/launchd/ai.openclaw.sidecars.plist.template"
    ).read_text(encoding="utf-8")
    launchd_browserlab = (
        REPO_ROOT / "platform/launchd/ai.openclaw.browserlab.plist.template"
    ).read_text(encoding="utf-8")
    launchd_maintenance = (
        REPO_ROOT / "platform/launchd/ai.openclaw.maintenance.plist.template"
    ).read_text(encoding="utf-8")
    launchd_backup_create = (
        REPO_ROOT / "platform/launchd/ai.openclaw.backup-create.plist.template"
    ).read_text(encoding="utf-8")
    launchd_backup_verify = (
        REPO_ROOT / "platform/launchd/ai.openclaw.backup-verify.plist.template"
    ).read_text(encoding="utf-8")

    assert "scripts/ops/" not in gateway
    assert "scripts/ops/" not in sidecars
    assert "__PYTHON_EXECUTABLE__ -m clawops" in gateway
    assert "__PYTHON_EXECUTABLE__ -m clawops" in browserlab
    assert "__PYTHON_EXECUTABLE__ -m clawops" in maintenance_service
    assert "__PYTHON_EXECUTABLE__ -m clawops" in backup_create_service
    assert "__PYTHON_EXECUTABLE__ -m clawops" in backup_verify_service
    assert "__PYTHON_EXECUTABLE__ -m clawops" in sidecars
    assert "openclaw-sidecars.service" in gateway
    assert "Unit=openclaw-maintenance.service" in maintenance_timer
    assert "Unit=openclaw-backup-create.service" in backup_create_timer
    assert "Unit=openclaw-backup-verify.service" in backup_verify_timer
    assert "__PYTHON_EXECUTABLE__" in launchd_gateway
    assert "__PYTHON_EXECUTABLE__" in launchd_maintenance
    assert "__PYTHON_EXECUTABLE__" in launchd_backup_create
    assert "__PYTHON_EXECUTABLE__" in launchd_backup_verify
    assert "backup-create" not in launchd_maintenance
    assert "backup-verify latest" not in launchd_maintenance
    assert "prune-retention" in launchd_maintenance
    assert "backup-create" in launchd_backup_create
    assert "backup-verify latest" in launchd_backup_verify
    assert (
        "Environment=PATH=%h/.config/varlock/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        in gateway
    )
    assert (
        "Environment=PATH=%h/.config/varlock/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        in sidecars
    )
    assert "<key>PATH</key>" in launchd_gateway
    assert "<key>KeepAlive</key>\n    <false/>" in launchd_sidecars
    assert "<string>ops</string>\n      <string>--asset-root</string>" in launchd_gateway
    assert "<string>ops</string>\n      <string>--asset-root</string>" in launchd_sidecars
    assert "<string>ops</string>\n      <string>--asset-root</string>" in launchd_browserlab


def test_ci_workflows_do_not_call_root_scripts_directory() -> None:
    workflow_dir = REPO_ROOT / ".github" / "workflows"
    for workflow_path in workflow_dir.glob("*.yml"):
        text = workflow_path.read_text(encoding="utf-8")
        assert "./scripts/" not in text, workflow_path.as_posix()


def test_ci_workflows_use_uv_default_dev_group() -> None:
    workflow_dir = REPO_ROOT / ".github" / "workflows"
    for workflow_path in workflow_dir.glob("*.yml"):
        text = workflow_path.read_text(encoding="utf-8")
        assert "--extra dev" not in text, workflow_path.as_posix()


def test_operator_docs_use_uv_default_dev_group() -> None:
    for relative_path in (
        "README.md",
        "QUICKSTART.md",
        "SETUP_GUIDE.md",
        "platform/docs/HOST_PLATFORMS.md",
    ):
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "--extra dev" not in text, relative_path
