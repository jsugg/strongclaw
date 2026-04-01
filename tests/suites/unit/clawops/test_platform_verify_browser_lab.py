"""Unit tests for platform verification targets."""

from __future__ import annotations

import json
import pathlib

import pytest

from clawops import platform_verify
from tests.plugins.infrastructure.context import TestContext


def _write_browser_lab_compose(
    path: pathlib.Path,
    *,
    proxy_host: str = "127.0.0.1",
    playwright_host: str = "127.0.0.1",
) -> None:
    """Write a minimal browser-lab compose contract."""
    path.write_text(
        "\n".join(
            (
                "services:",
                "  browserlab-proxy:",
                "    image: ubuntu/squid:latest",
                "    ports:",
                f'      - "{proxy_host}:3128:3128"',
                "  browserlab-playwright:",
                "    image: mcr.microsoft.com/playwright:v1.41.1-jammy",
                "    depends_on:",
                "      - browserlab-proxy",
                "    ports:",
                f'      - "{playwright_host}:9222:9222"',
            )
        )
        + "\n",
        encoding="utf-8",
    )


def test_verify_browser_lab_contract_passes_when_runtime_is_skipped(tmp_path: pathlib.Path) -> None:
    compose_path = tmp_path / "browser-lab.yaml"
    _write_browser_lab_compose(compose_path)

    report = platform_verify.verify_browser_lab(compose_path=compose_path, skip_runtime=True)

    assert report.ok is True
    assert report.name == "browser-lab"
    assert any(check.name == "runtime-probes" for check in report.checks)


def test_verify_browser_lab_contract_rejects_non_loopback_bindings(tmp_path: pathlib.Path) -> None:
    compose_path = tmp_path / "browser-lab.yaml"
    _write_browser_lab_compose(compose_path, proxy_host="0.0.0.0")

    report = platform_verify.verify_browser_lab(compose_path=compose_path, skip_runtime=True)

    assert report.ok is False
    assert any(
        check.name == "browserlab-proxy-port" and "must bind loopback-only" in check.message
        for check in report.checks
    )


def test_main_dispatches_browser_lab_target(
    tmp_path: pathlib.Path,
    test_context: TestContext,
    capsys: pytest.CaptureFixture[str],
) -> None:
    compose_path = tmp_path / "browser-lab.yaml"
    _write_browser_lab_compose(compose_path)
    captured: dict[str, object] = {}

    def _resolve_asset_root_argument(*_args: object, **_kwargs: object) -> pathlib.Path:
        return tmp_path

    def _resolve_asset_path(path: str, *, repo_root: pathlib.Path) -> pathlib.Path:
        assert repo_root == tmp_path
        assert path == "platform/compose/docker-compose.browser-lab.yaml"
        return compose_path

    def _verify_browser_lab(
        *,
        compose_path: pathlib.Path,
        skip_runtime: bool,
    ) -> platform_verify.VerificationReport:
        captured["compose"] = compose_path
        captured["skip_runtime"] = skip_runtime
        return platform_verify.VerificationReport(
            name="browser-lab",
            checks=[platform_verify.Check(name="browserlab-proxy-port", ok=True, message="ok")],
        )

    test_context.patch.patch_object(
        platform_verify,
        "resolve_asset_root_argument",
        new=_resolve_asset_root_argument,
    )
    test_context.patch.patch_object(platform_verify, "resolve_asset_path", new=_resolve_asset_path)
    test_context.patch.patch_object(platform_verify, "verify_browser_lab", new=_verify_browser_lab)

    exit_code = platform_verify.main(["browser-lab", "--skip-runtime"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured == {"compose": compose_path, "skip_runtime": True}
    assert payload["name"] == "browser-lab"
    assert payload["ok"] is True
