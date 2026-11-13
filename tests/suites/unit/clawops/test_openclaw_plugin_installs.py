"""Unit coverage for installing trusted first-party plugins through `openclaw plugins install`."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Sequence

import pytest

from clawops import openclaw_plugin_installs as installs
from clawops.platform_compat import NODE_TOOL_PINS
from clawops.strongclaw_runtime import CommandError, ExecResult
from tests.plugins.infrastructure.context import TestContext

_REPO = pathlib.Path("/opt/strongclaw")
_PIN = NODE_TOOL_PINS["@openclaw/acpx"]
_SPEC = f"npm:@openclaw/acpx@{_PIN}"
_ACP_CONFIG: dict[str, object] = {
    "agents": {"list": [{"id": "coder", "runtime": {"type": "acp", "acp": {"backend": "acpx"}}}]}
}


def _result(argv: Sequence[str], *, returncode: int = 0, stdout: str = "") -> ExecResult:
    return ExecResult(
        argv=tuple(argv), returncode=returncode, stdout=stdout, stderr="", duration_ms=1
    )


def _inspect_payload(*, version: str, trusted: bool = True, source: str = "npm") -> str:
    payload = {
        "plugin": {"id": "acpx", "trustedOfficialInstall": trusted, "origin": "global"},
        "install": {
            "source": source,
            "spec": f"@openclaw/acpx@{version}",
            "resolvedVersion": version,
        },
    }
    # OpenClaw prints config warnings and plugin log lines ahead of the JSON document.
    return "[plugins] registered\n" + json.dumps(payload)


class _FakeOpenClaw:
    def __init__(self, *, inspect: ExecResult | None, install_ok: bool = True) -> None:
        self.calls: list[list[str]] = []
        self._inspect = inspect
        self._install_ok = install_ok

    def __call__(
        self, repo_root: pathlib.Path, arguments: Sequence[str], **_kwargs: object
    ) -> ExecResult:
        assert repo_root == _REPO
        self.calls.append(list(arguments))
        if arguments[:2] == ["plugins", "inspect"]:
            return self._inspect or _result(arguments, returncode=1)
        return _result(arguments, returncode=0 if self._install_ok else 1)


def _patch(test_context: TestContext, fake: _FakeOpenClaw) -> None:
    test_context.patch.patch_object(installs, "run_openclaw_command", new=fake)


def test_missing_plugin_is_installed_at_the_lock_pin(test_context: TestContext) -> None:
    fake = _FakeOpenClaw(inspect=None)
    _patch(test_context, fake)

    assert installs.ensure_openclaw_plugin_installs(_REPO, _ACP_CONFIG) == ["acpx"]
    assert fake.calls[-1] == ["plugins", "install", _SPEC, "--pin"]


def test_trusted_install_at_the_pin_is_left_alone(test_context: TestContext) -> None:
    fake = _FakeOpenClaw(inspect=_result(["inspect"], stdout=_inspect_payload(version=_PIN)))
    _patch(test_context, fake)

    assert installs.ensure_openclaw_plugin_installs(_REPO, _ACP_CONFIG) == []
    assert fake.calls == [["plugins", "inspect", "acpx", "--json"]]


@pytest.mark.parametrize(
    "stdout",
    [
        _inspect_payload(version="2026.1.1"),
        _inspect_payload(version=_PIN, trusted=False),
        _inspect_payload(version=_PIN, source="path"),
    ],
    ids=["other-version", "untrusted", "non-npm-source"],
)
def test_drifted_install_is_replaced(test_context: TestContext, stdout: str) -> None:
    fake = _FakeOpenClaw(inspect=_result(["inspect"], stdout=stdout))
    _patch(test_context, fake)

    assert installs.ensure_openclaw_plugin_installs(_REPO, _ACP_CONFIG) == ["acpx"]
    assert fake.calls[-1] == ["plugins", "install", _SPEC, "--pin", "--force"]


def test_failed_install_raises(test_context: TestContext) -> None:
    _patch(test_context, _FakeOpenClaw(inspect=None, install_ok=False))

    with pytest.raises(CommandError, match="plugins install"):
        installs.ensure_openclaw_plugin_installs(_REPO, _ACP_CONFIG)


def test_configs_without_acp_never_call_openclaw(test_context: TestContext) -> None:
    fake = _FakeOpenClaw(inspect=None)
    _patch(test_context, fake)

    assert (
        installs.ensure_openclaw_plugin_installs(_REPO, {"agents": {"list": [{"id": "a"}]}}) == []
    )
    assert fake.calls == []
