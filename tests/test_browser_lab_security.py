"""Regression tests for the browser-lab local-only operating model."""

from __future__ import annotations

import pathlib

from clawops.common import load_yaml
from clawops.openclaw_config import render_openclaw_profile


def test_browser_lab_ports_publish_to_loopback_only() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    compose = load_yaml(repo_root / "platform/compose/docker-compose.browser-lab.yaml")
    services = compose["services"]

    assert services["browserlab-proxy"]["ports"] == ["127.0.0.1:3128:3128"]
    assert services["browserlab-playwright"]["ports"] == ["127.0.0.1:9222:9222"]


def test_browser_lab_config_targets_local_cdp() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    config_text = (repo_root / "platform/configs/openclaw/60-browser-lab.json5").read_text(
        encoding="utf-8"
    )

    assert '"cdpUrl": "http://127.0.0.1:9222"' in config_text


def test_browser_lab_docs_forbid_tunneling_cdp_port() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    docs_text = (repo_root / "platform/docs/BROWSER_LAB.md").read_text(encoding="utf-8")

    assert "tunnel `9222` or `3128`" in docs_text
    assert "ssh -N -L 18789:127.0.0.1:18789" in docs_text


def test_browser_lab_profile_does_not_render_qmd_backend() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    rendered = render_openclaw_profile(
        profile_name="browser-lab",
        repo_root=repo_root,
        home_dir=pathlib.Path.home(),
        user_timezone="UTC",
    )

    assert rendered["memory"] == {"citations": "auto"}
    assert rendered["plugins"]["slots"]["memory"] == "memory-core"
    assert rendered["browser"]["defaultProfile"] == "browserlab"


def test_loopback_binding_checker_tracks_sensitive_ports() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    script_text = (repo_root / "scripts/ops/check_loopback_bindings.sh").read_text(encoding="utf-8")

    for port in ("18789", "5432", "4000", "4318", "9464", "3128", "9222", "3000"):
        assert port in script_text
