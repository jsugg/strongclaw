"""Lightweight docs and config parity tests."""

from __future__ import annotations

import pathlib
import re

from clawops.context_service import load_config

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


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
    repo_root = _repo_root()
    markdown_files = _official_markdown_files(repo_root)
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
    repo_root = _repo_root()
    config = load_config(repo_root / "platform/configs/context/context-service.yaml")
    assert config.include_globs
    assert config.exclude_globs
    assert "platform/plugins/memory-lancedb-pro/**" in config.exclude_globs


def test_operator_docs_surface_platform_verification_commands() -> None:
    repo_root = _repo_root()
    quickstart = (repo_root / "QUICKSTART.md").read_text(encoding="utf-8")
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    setup = (repo_root / "SETUP_GUIDE.md").read_text(encoding="utf-8")
    host_platforms = (repo_root / "platform/docs/HOST_PLATFORMS.md").read_text(encoding="utf-8")
    macos_runbook = (repo_root / "platform/docs/runbooks/macos-service-user-and-ssh.md").read_text(
        encoding="utf-8"
    )
    linux_runbook = (
        repo_root / "platform/docs/runbooks/linux-runtime-user-and-systemd.md"
    ).read_text(encoding="utf-8")

    assert "clawops setup" in readme
    assert "./scripts/bootstrap/verify_sidecars.sh" in quickstart
    assert "./scripts/bootstrap/verify_channels.sh" in quickstart
    assert "./scripts/bootstrap/verify_channels.sh" in setup
    assert "./scripts/bootstrap/verify_observability.sh" in quickstart
    assert "./scripts/bootstrap/verify_observability.sh" in setup
    assert "clawops setup" in setup
    assert "clawops setup" in host_platforms
    assert "make doctor" in quickstart
    assert "make doctor" in setup
    assert "make install" in quickstart
    assert "macOS-first rollout before Linux" not in host_platforms
    assert "./scripts/bootstrap/create_openclawsvc.sh" in macos_runbook
    assert "./scripts/bootstrap/create_openclawsvc.sh" in linux_runbook


def test_operator_docs_surface_supported_hypermemory_path() -> None:
    repo_root = _repo_root()
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    quickstart = (repo_root / "QUICKSTART.md").read_text(encoding="utf-8")
    setup = (repo_root / "SETUP_GUIDE.md").read_text(encoding="utf-8")
    usage = (repo_root / "USAGE_GUIDE.md").read_text(encoding="utf-8")
    memory_doc = (repo_root / "platform/docs/HYPERMEMORY.md").read_text(encoding="utf-8")
    secrets = (repo_root / "platform/docs/SECRETS_AND_ENV.md").read_text(encoding="utf-8")
    routing = (repo_root / "platform/docs/MODEL_ROUTING.md").read_text(encoding="utf-8")

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
    assert "./scripts/bootstrap/verify_hypermemory.sh" in memory_doc
    assert "HYPERMEMORY_EMBEDDING_MODEL" in quickstart
    assert "HYPERMEMORY_EMBEDDING_MODEL" in secrets
    assert "hypermemory-embedding" in routing


def test_hypermemory_docs_surface_memory_pro_migration_bridge() -> None:
    repo_root = _repo_root()
    memory_doc = (repo_root / "platform/docs/HYPERMEMORY.md").read_text(encoding="utf-8")
    usage = (repo_root / "USAGE_GUIDE.md").read_text(encoding="utf-8")

    assert "clawops memory migrate-hypermemory-to-pro" in memory_doc
    assert "clawops memory import-pro-snapshot" in memory_doc
    assert "clawops memory verify-pro-parity" in memory_doc
    assert "openclaw memory-pro import" in memory_doc
    assert "--mode openclaw" in memory_doc
    assert "clawops memory import-pro-snapshot" in usage


def test_operator_docs_surface_approvals_cli() -> None:
    repo_root = _repo_root()
    usage = (repo_root / "USAGE_GUIDE.md").read_text(encoding="utf-8")
    wrappers = (repo_root / "platform/docs/POLICY_ENGINE_AND_WRAPPERS.md").read_text(
        encoding="utf-8"
    )

    assert "clawops approvals approve" in usage
    assert "clawops approvals delegate" in usage
    assert "clawops approvals approve" in wrappers
    assert "clawops approvals delegate" in wrappers


def test_operator_docs_surface_repo_memory_and_skill_commands() -> None:
    repo_root = _repo_root()
    usage = (repo_root / "USAGE_GUIDE.md").read_text(encoding="utf-8")
    repo_doc = (repo_root / "repo/README.md").read_text(encoding="utf-8")
    ci_doc = (repo_root / "platform/docs/CI_AND_SECURITY.md").read_text(encoding="utf-8")

    assert "clawops memory migrate-hypermemory-to-pro" in usage
    assert "clawops memory verify-pro-parity" in usage
    assert "clawops repo --repo-root" in usage
    assert "clawops worktree --repo-root" in usage
    assert "clawops skills scan" in usage
    assert "clawops skills promote" in usage
    assert "clawops repo --repo-root" in repo_doc
    assert "clawops worktree --repo-root" in repo_doc
    assert "dependency-submission.yml" in ci_doc
    assert "memory-plugin-verification.yml" in ci_doc
