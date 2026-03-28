"""Lightweight docs and config parity tests."""

from __future__ import annotations

import pathlib
import re

from clawops.context.codebase.service import load_config
from tests.utils.helpers.repo import REPO_ROOT

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _official_markdown_files(repo_root: pathlib.Path) -> list[pathlib.Path]:
    root_markdown = [
        repo_root / "README.md",
        repo_root / "QUICKSTART.md",
        repo_root / "SETUP_GUIDE.md",
        repo_root / "USAGE_GUIDE.md",
    ]
    platform_markdown = [
        path
        for path in (repo_root / "platform").rglob("*.md")
        if "platform/plugins/" not in path.as_posix()
    ]
    return root_markdown + platform_markdown


def test_markdown_relative_links_resolve() -> None:
    markdown_files = _official_markdown_files(REPO_ROOT)
    for markdown_file in markdown_files:
        text = markdown_file.read_text(encoding="utf-8")
        for target in LINK_RE.findall(text):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_text = target.split("#", 1)[0]
            if not path_text:
                continue
            resolved = (markdown_file.parent / path_text).resolve()
            assert resolved.exists(), f"broken link in {markdown_file}: {target}"


def test_shipped_context_config_loads() -> None:
    config = load_config(REPO_ROOT / "platform/configs/context/codebase.yaml")
    assert config.include_globs
    assert config.exclude_globs
    assert "platform/plugins/memory-lancedb-pro/**" in config.exclude_globs


def test_operator_docs_surface_codebase_context_commands() -> None:
    quickstart = (REPO_ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
    setup = (REPO_ROOT / "SETUP_GUIDE.md").read_text(encoding="utf-8")
    usage = (REPO_ROOT / "USAGE_GUIDE.md").read_text(encoding="utf-8")
    skill = (
        REPO_ROOT / "platform" / "skills" / "local" / "repo-context-pack" / "SKILL.md"
    ).read_text(encoding="utf-8")
    context_doc = (REPO_ROOT / "platform" / "docs" / "CONTEXT_SERVICE.md").read_text(
        encoding="utf-8"
    )

    assert "clawops context codebase index" in quickstart
    assert "platform/configs/context/codebase.yaml" in quickstart
    assert "clawops context codebase index" in setup
    assert "clawops context codebase query" in setup
    assert "clawops context codebase index" in usage
    assert "clawops context codebase query" in usage
    assert "clawops context codebase pack" in usage
    assert "clawops context codebase index" in skill
    assert "clawops context codebase benchmark" in context_doc
    assert "platform/configs/context/benchmarks/codebase.yaml" in context_doc
    for markdown_file in _official_markdown_files(REPO_ROOT):
        text = markdown_file.read_text(encoding="utf-8")
        assert "clawops context index" not in text
        assert "clawops context query" not in text
        assert "clawops context pack" not in text


def test_operator_docs_surface_platform_verification_commands() -> None:
    quickstart = (REPO_ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    setup = (REPO_ROOT / "SETUP_GUIDE.md").read_text(encoding="utf-8")
    host_platforms = (REPO_ROOT / "platform/docs/HOST_PLATFORMS.md").read_text(encoding="utf-8")
    macos_runbook = (REPO_ROOT / "platform/docs/runbooks/macos-service-user-and-ssh.md").read_text(
        encoding="utf-8"
    )
    linux_runbook = (
        REPO_ROOT / "platform/docs/runbooks/linux-runtime-user-and-systemd.md"
    ).read_text(encoding="utf-8")

    assert "clawops setup" in readme
    assert "clawops verify-platform sidecars --skip-runtime" in quickstart
    assert "clawops verify-platform channels" in quickstart
    assert "clawops verify-platform channels" in setup
    assert "clawops verify-platform observability" in quickstart
    assert "clawops verify-platform observability" in setup
    assert "clawops setup" in setup
    assert "clawops setup" in host_platforms
    assert "make doctor" in quickstart
    assert "make doctor" in setup
    assert "make install" in quickstart
    assert "macOS-first rollout before Linux" not in host_platforms
    assert "platform-native user-management tooling" in macos_runbook
    assert "platform-native user-management tooling" in linux_runbook


def test_operator_docs_surface_repo_local_dev_sidecar_state_commands() -> None:
    quickstart = (REPO_ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
    usage = (REPO_ROOT / "USAGE_GUIDE.md").read_text(encoding="utf-8")
    recovery = (REPO_ROOT / "platform/docs/BACKUP_AND_RECOVERY.md").read_text(encoding="utf-8")

    assert "clawops ops sidecars up --repo-local-state" in quickstart
    assert "clawops ops sidecars up --repo-local-state" in usage
    assert "clawops ops sidecars down --repo-local-state" in quickstart
    assert "clawops ops sidecars down --repo-local-state" in usage
    assert "clawops ops prune-qdrant-test-collections" in quickstart
    assert "clawops ops prune-qdrant-test-collections" in usage
    assert "clawops ops prune-qdrant-test-collections" in recovery
    assert "clawops ops reset-compose-state --component qdrant" in quickstart
    assert "clawops ops reset-compose-state --component qdrant" in usage
    assert "clawops ops reset-compose-state --component qdrant" in recovery


def test_operator_docs_surface_supported_hypermemory_path() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (REPO_ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
    setup = (REPO_ROOT / "SETUP_GUIDE.md").read_text(encoding="utf-8")
    usage = (REPO_ROOT / "USAGE_GUIDE.md").read_text(encoding="utf-8")
    memory_doc = (REPO_ROOT / "platform/docs/HYPERMEMORY.md").read_text(encoding="utf-8")
    secrets = (REPO_ROOT / "platform/docs/SECRETS_AND_ENV.md").read_text(encoding="utf-8")
    routing = (REPO_ROOT / "platform/docs/MODEL_ROUTING.md").read_text(encoding="utf-8")

    assert "hypermemory" in readme
    assert "hypermemory" in quickstart
    assert "hypermemory" in setup
    assert "hypermemory" in usage
    assert "openclaw-qmd" in readme
    assert "openclaw-qmd" in quickstart
    assert "openclaw-qmd" in setup
    assert "openclaw-qmd" in usage
    assert "clawops setup --profile hypermemory" in memory_doc
    assert "clawops config memory --set-profile hypermemory" in memory_doc
    assert "clawops config memory --set-profile openclaw-qmd" in memory_doc
    assert (
        "clawops hypermemory --config platform/configs/memory/hypermemory.yaml verify" in memory_doc
    )
    assert "HYPERMEMORY_EMBEDDING_MODEL" in quickstart
    assert "HYPERMEMORY_EMBEDDING_MODEL" in secrets
    assert "hypermemory-embedding" in routing


def test_hypermemory_docs_surface_memory_pro_migration_bridge() -> None:
    memory_doc = (REPO_ROOT / "platform/docs/HYPERMEMORY.md").read_text(encoding="utf-8")
    usage = (REPO_ROOT / "USAGE_GUIDE.md").read_text(encoding="utf-8")

    assert "clawops memory migrate-hypermemory-to-pro" in memory_doc
    assert "clawops memory import-pro-snapshot" in memory_doc
    assert "clawops memory verify-pro-parity" in memory_doc
    assert "openclaw memory-pro import" in memory_doc
    assert "--mode openclaw" in memory_doc
    assert "clawops memory import-pro-snapshot" in usage


def test_operator_docs_surface_approvals_cli() -> None:
    usage = (REPO_ROOT / "USAGE_GUIDE.md").read_text(encoding="utf-8")
    wrappers = (REPO_ROOT / "platform/docs/POLICY_ENGINE_AND_WRAPPERS.md").read_text(
        encoding="utf-8"
    )

    assert "clawops approvals approve" in usage
    assert "clawops approvals delegate" in usage
    assert "clawops approvals approve" in wrappers
    assert "clawops approvals delegate" in wrappers


def test_operator_docs_surface_repo_memory_and_skill_commands() -> None:
    usage = (REPO_ROOT / "USAGE_GUIDE.md").read_text(encoding="utf-8")
    repo_doc = (REPO_ROOT / "repo/README.md").read_text(encoding="utf-8")
    ci_doc = (REPO_ROOT / "platform/docs/CI_AND_SECURITY.md").read_text(encoding="utf-8")

    assert "clawops memory migrate-hypermemory-to-pro" in usage
    assert "clawops memory verify-pro-parity" in usage
    assert "clawops repo doctor" in usage
    assert "clawops worktree list" in usage
    assert "clawops skills scan" in usage
    assert "clawops skills promote" in usage
    assert "clawops repo doctor" in repo_doc
    assert "clawops worktree list" in repo_doc
    assert "dependency-submission.yml" in ci_doc
    assert "memory-plugin-verification.yml" in ci_doc


def test_operator_docs_no_longer_surface_root_shell_entrypoints() -> None:
    for markdown_file in _official_markdown_files(REPO_ROOT):
        text = markdown_file.read_text(encoding="utf-8")
        assert "./scripts/" not in text
