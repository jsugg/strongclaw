"""Unit coverage for the locked node-tools installer."""

from __future__ import annotations

import json
import os
import pathlib
import sys
from collections.abc import Sequence

import pytest

from clawops import strongclaw_node_tools as node_tools
from clawops.strongclaw_node_tools import (
    LocalPrefixWriter,
    NodeToolsBundle,
    NodeToolsError,
    NodeToolsInstaller,
    NodeToolsLayout,
    SudoPrefixWriter,
    ensure_node_tools_installed,
    is_transient_npm_failure,
    load_node_tools_bundle,
    npm_ci_retry_delay_seconds,
    resolve_npm_global_prefix,
    run_npm_ci,
)
from clawops.strongclaw_runtime import ExecResult
from tests.plugins.infrastructure.context import TestContext
from tests.utils.helpers.repo import REPO_ROOT

_FAKE_NPM = """
import json
import os
import pathlib
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_NPM_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": args, "cwd": os.getcwd()}) + "\\n")
if args[:2] == ["prefix", "-g"]:
    print(os.environ["FAKE_NPM_PREFIX"])
    raise SystemExit(0)
if not args or args[0] != "ci":
    raise SystemExit(f"unexpected npm invocation: {args}")
failures = pathlib.Path(os.environ["FAKE_NPM_FAILURES"])
queue = failures.read_text(encoding="utf-8").split() if failures.is_file() else []
if queue:
    failures.write_text(" ".join(queue[1:]), encoding="utf-8")
    print(f"npm error code {queue[0]}", file=sys.stderr)
    raise SystemExit(1)
lock = json.loads(pathlib.Path("package-lock.json").read_text(encoding="utf-8"))
reported = os.environ.get("FAKE_NPM_REPORTED_VERSION", "")
bin_dir = pathlib.Path("node_modules", ".bin")
bin_dir.mkdir(parents=True, exist_ok=True)
for name, version in lock["packages"][""]["dependencies"].items():
    package_dir = pathlib.Path("node_modules", name)
    package_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "version": version, "type": "module"}
    (package_dir / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    if name == "openclaw":
        api = package_dir / "dist" / "extensionAPI.js"
        api.parent.mkdir(parents=True, exist_ok=True)
        api.write_text(
            f"export async function runEmbeddedPiAgent() {{ return '{version}'; }}\\n",
            encoding="utf-8",
        )
    for bin_name, relative in lock["packages"]["node_modules/" + name]["bin"].items():
        target = package_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"#!/bin/sh\\necho '{name} {reported or version}'\\n", encoding="utf-8")
        target.chmod(0o755)
        (bin_dir / bin_name).symlink_to(os.path.relpath(target, bin_dir))
"""
_INTEGRITY = "sha512-" + "A" * 86 + "=="
# None deletes the field instead of setting it.
type LockFieldValue = str | bool | dict[str, str] | None


class _FakeNpm:
    """Handle to the fake `npm` executable placed first on PATH."""

    def __init__(self, test_context: TestContext, root: pathlib.Path) -> None:
        bin_dir = root / "fake-bin"
        bin_dir.mkdir(parents=True)
        npm_path = bin_dir / "npm"
        npm_path.write_text(f"#!{sys.executable}\n{_FAKE_NPM}", encoding="utf-8")
        npm_path.chmod(0o755)
        self.prefix = root / "prefix"
        (self.prefix / "bin").mkdir(parents=True)
        self.log_path = root / "npm-log.jsonl"
        self.failures_path = root / "npm-failures.txt"
        self._test_context = test_context
        test_context.env.prepend_path(bin_dir)
        test_context.env.set("FAKE_NPM_LOG", str(self.log_path))
        test_context.env.set("FAKE_NPM_PREFIX", str(self.prefix))
        test_context.env.set("FAKE_NPM_FAILURES", str(self.failures_path))
        test_context.env.remove("FAKE_NPM_REPORTED_VERSION")

    def fail_next(self, *codes: str) -> None:
        """Make the next `npm ci` invocations fail with *codes*, one per call."""
        self.failures_path.write_text(" ".join(codes), encoding="utf-8")

    def report_version(self, version: str) -> None:
        """Make installed bins report *version* instead of the locked one."""
        self._test_context.env.set("FAKE_NPM_REPORTED_VERSION", version)

    def calls(self) -> list[list[str]]:
        """Return the argv of every recorded npm invocation."""
        if not self.log_path.is_file():
            return []
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        return [list(json.loads(line)["argv"]) for line in lines]

    def ci_calls(self) -> list[list[str]]:
        """Return the argv of every recorded `npm ci`."""
        return [call for call in self.calls() if call[:1] == ["ci"]]


class _RecordingWriter(LocalPrefixWriter):
    """Local writer that records every mutation in order."""

    def __init__(self) -> None:
        self.operations: list[tuple[str, pathlib.Path]] = []

    def rename(self, source: pathlib.Path, target: pathlib.Path) -> None:
        self.operations.append(("rename", target))
        super().rename(source, target)

    def replace_symlink(self, link: pathlib.Path, target: str) -> None:
        self.operations.append(("replace_symlink", link))
        super().replace_symlink(link, target)

    def remove(self, path: pathlib.Path) -> None:
        self.operations.append(("remove", path))
        super().remove(path)


def _lock_entry(name: str, version: str, bins: dict[str, str]) -> dict[str, object]:
    return {
        "version": version,
        "resolved": f"https://registry.npmjs.org/{name}/-/{name}-{version}.tgz",
        "integrity": _INTEGRITY,
        "bin": bins,
    }


def _write_bundle(
    asset_dir: pathlib.Path,
    *,
    openclaw: str = "2026.3.13",
    acpx: str = "0.3.0",
) -> NodeToolsBundle:
    """Write a minimal valid manifest plus lock and return the loaded bundle."""
    asset_dir.mkdir(parents=True, exist_ok=True)
    dependencies = {"acpx": acpx, "openclaw": openclaw}
    manifest = {"name": "strongclaw-node-tools", "private": True, "dependencies": dependencies}
    lock = {
        "name": "strongclaw-node-tools",
        "lockfileVersion": 3,
        "requires": True,
        "packages": {
            "": {"name": "strongclaw-node-tools", "dependencies": dependencies},
            "node_modules/acpx": _lock_entry("acpx", acpx, {"acpx": "dist/cli.js"}),
            "node_modules/openclaw": _lock_entry(
                "openclaw", openclaw, {"openclaw": "openclaw.mjs"}
            ),
            "node_modules/libsignal": {
                "version": "2.0.1",
                "resolved": "git+ssh://git@github.com/whiskeysockets/libsignal-node.git#"
                + "b" * 40,
            },
        },
    }
    (asset_dir / "package.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (asset_dir / "package-lock.json").write_text(json.dumps(lock, indent=2), encoding="utf-8")
    return load_node_tools_bundle(asset_dir)


def _run_bin(prefix: pathlib.Path, bin_name: str) -> str:
    result = node_tools.run_command([str(prefix / "bin" / bin_name)], timeout_seconds=30)
    assert result.ok, result
    return result.stdout.strip()


def _install(
    bundle: NodeToolsBundle,
    prefix: pathlib.Path,
    writer: LocalPrefixWriter | None = None,
) -> node_tools.NodeToolsInstallResult:
    return NodeToolsInstaller(
        bundle, NodeToolsLayout(prefix), writer or LocalPrefixWriter()
    ).ensure()


def _assert_compat_links(layout: NodeToolsLayout, bundle: NodeToolsBundle) -> None:
    """Legacy global package paths must be symlinks resolving into the active release."""
    active_release = layout.release_dir(bundle.digest).resolve()
    for package in bundle.packages:
        link = layout.compat_package_link(package.name)
        assert link.is_symlink(), link
        assert os.readlink(link) == f"../strongclaw-node-tools/current/node_modules/{package.name}"
        assert active_release in link.resolve().parents
        manifest = json.loads((link / "package.json").read_text(encoding="utf-8"))
        assert manifest["version"] == package.version


@pytest.fixture
def fake_npm(test_context: TestContext, tmp_path: pathlib.Path) -> _FakeNpm:
    return _FakeNpm(test_context, tmp_path)


@pytest.fixture
def recorded_sleeps(test_context: TestContext) -> list[float]:
    sleeps: list[float] = []
    test_context.patch.patch_object(node_tools.time, "sleep", new=sleeps.append)
    return sleeps


def test_install_uses_npm_ci_links_bins_and_reruns_as_noop(
    fake_npm: _FakeNpm, tmp_path: pathlib.Path
) -> None:
    """First run installs via `npm ci` and links bins; a re-run with the same lock is a no-op."""
    bundle = _write_bundle(tmp_path / "assets")
    layout = NodeToolsLayout(fake_npm.prefix)

    first = _install(bundle, fake_npm.prefix)

    assert first.changed is True
    assert fake_npm.ci_calls() == [["ci", "--omit=dev", "--no-audit", "--no-fund"]]
    assert not any("install" in call or "-g" in call for call in fake_npm.calls())
    assert os.readlink(layout.current_link) == bundle.digest
    for bin_name in ("openclaw", "acpx"):
        link = layout.bin_link(bin_name)
        assert os.readlink(link) == (
            f"../lib/strongclaw-node-tools/current/node_modules/.bin/{bin_name}"
        )
    assert _run_bin(fake_npm.prefix, "openclaw") == "openclaw 2026.3.13"
    assert _run_bin(fake_npm.prefix, "acpx") == "acpx 0.3.0"
    _assert_compat_links(layout, bundle)
    release_inode = layout.release_dir(bundle.digest).stat().st_ino
    assert sorted(path.name for path in layout.tool_root.iterdir()) == sorted(
        ["current", bundle.digest]
    )

    writer = _RecordingWriter()
    second = _install(bundle, fake_npm.prefix, writer)

    assert second.changed is False
    assert writer.operations == []
    assert len(fake_npm.ci_calls()) == 1
    assert layout.release_dir(bundle.digest).stat().st_ino == release_inode
    _assert_compat_links(layout, bundle)


def test_install_migrates_legacy_global_packages_without_a_missing_command_window(
    fake_npm: _FakeNpm, tmp_path: pathlib.Path
) -> None:
    """Legacy bins swap atomically first; legacy package dirs then become compat links."""
    bundle = _write_bundle(tmp_path / "assets")
    layout = NodeToolsLayout(fake_npm.prefix)
    for name, relative in (("openclaw", "openclaw.mjs"), ("acpx", "dist/cli.js")):
        legacy_entry = layout.compat_package_link(name) / relative
        legacy_entry.parent.mkdir(parents=True)
        legacy_entry.write_text("#!/bin/sh\necho legacy\n", encoding="utf-8")
        legacy_entry.chmod(0o755)
        layout.bin_link(name).symlink_to(f"../lib/node_modules/{name}/{relative}")
    writer = _RecordingWriter()

    result = _install(bundle, fake_npm.prefix, writer)

    assert result.changed is True
    assert _run_bin(fake_npm.prefix, "openclaw") == "openclaw 2026.3.13"
    _assert_compat_links(layout, bundle)
    assert sorted(path.name for path in layout.tool_root.iterdir()) == sorted(
        ["current", bundle.digest]
    )
    bin_links = {layout.bin_link("openclaw"), layout.bin_link("acpx")}
    assert all(operation != "remove" for operation, path in writer.operations if path in bin_links)
    last_bin_swap = max(
        index
        for index, (operation, path) in enumerate(writer.operations)
        if operation == "replace_symlink" and path in bin_links
    )
    compat_paths = {layout.compat_package_link("openclaw"), layout.compat_package_link("acpx")}
    first_compat_change = min(
        index
        for index, (_, path) in enumerate(writer.operations)
        if path in compat_paths or path.name.startswith(".retired-legacy-")
    )
    assert last_bin_swap < first_compat_change


def test_legacy_extension_api_path_resolves_into_the_active_release(
    fake_npm: _FakeNpm, tmp_path: pathlib.Path
) -> None:
    """Absolute `lib/node_modules/openclaw/...` consumers follow upgrades through `current`."""
    layout = NodeToolsLayout(fake_npm.prefix)
    legacy_api = layout.compat_package_link("openclaw") / "dist" / "extensionAPI.js"
    previous = _write_bundle(tmp_path / "v1")
    _install(previous, fake_npm.prefix)

    assert legacy_api.resolve().is_relative_to(layout.release_dir(previous.digest).resolve())

    candidate = _write_bundle(tmp_path / "v2", openclaw="2026.3.14")
    _install(candidate, fake_npm.prefix)

    assert legacy_api.resolve().is_relative_to(layout.release_dir(candidate.digest).resolve())
    assert "2026.3.14" in legacy_api.read_text(encoding="utf-8")


@pytest.mark.parametrize("occupant", ["file", "foreign-symlink"])
def test_install_refuses_to_clobber_foreign_bins(
    fake_npm: _FakeNpm, tmp_path: pathlib.Path, occupant: str
) -> None:
    """A bin path npm did not create for these packages fails fast and is left untouched."""
    bundle = _write_bundle(tmp_path / "assets")
    layout = NodeToolsLayout(fake_npm.prefix)
    foreign = layout.bin_link("openclaw")
    if occupant == "file":
        foreign.write_text("#!/bin/sh\n", encoding="utf-8")
    else:
        foreign.symlink_to("/opt/elsewhere/openclaw")

    with pytest.raises(NodeToolsError, match="refusing to replace"):
        _install(bundle, fake_npm.prefix)

    assert fake_npm.ci_calls() == []
    assert not layout.tool_root.exists()
    assert foreign.is_symlink() == (occupant == "foreign-symlink")


def test_failed_smoke_test_keeps_the_previous_release_active(
    fake_npm: _FakeNpm, tmp_path: pathlib.Path
) -> None:
    """A new lock whose bins misreport their version never replaces the working install."""
    layout = NodeToolsLayout(fake_npm.prefix)
    previous = _write_bundle(tmp_path / "v1")
    _install(previous, fake_npm.prefix)
    candidate = _write_bundle(tmp_path / "v2", acpx="0.3.1")
    fake_npm.report_version("9.9.9")

    with pytest.raises(NodeToolsError, match="smoke test failed"):
        _install(candidate, fake_npm.prefix)

    assert os.readlink(layout.current_link) == previous.digest
    assert _run_bin(fake_npm.prefix, "acpx") == "acpx 0.3.0"
    _assert_compat_links(layout, previous)
    assert sorted(path.name for path in layout.tool_root.iterdir()) == sorted(
        ["current", previous.digest]
    )


def test_lock_change_switches_release_and_prunes_the_old_one(
    fake_npm: _FakeNpm, tmp_path: pathlib.Path
) -> None:
    """A new lock digest installs a new release, activates it, and removes the superseded one."""
    layout = NodeToolsLayout(fake_npm.prefix)
    previous = _write_bundle(tmp_path / "v1")
    _install(previous, fake_npm.prefix)
    candidate = _write_bundle(tmp_path / "v2", acpx="0.3.1")

    result = _install(candidate, fake_npm.prefix)

    assert result.changed is True
    assert candidate.digest != previous.digest
    assert os.readlink(layout.current_link) == candidate.digest
    assert _run_bin(fake_npm.prefix, "acpx") == "acpx 0.3.1"
    assert not layout.release_dir(previous.digest).exists()
    _assert_compat_links(layout, candidate)


def test_unhealthy_active_release_is_rebuilt(fake_npm: _FakeNpm, tmp_path: pathlib.Path) -> None:
    """A damaged release with a matching digest is reinstalled instead of trusted."""
    bundle = _write_bundle(tmp_path / "assets")
    layout = NodeToolsLayout(fake_npm.prefix)
    _install(bundle, fake_npm.prefix)
    (layout.release_dir(bundle.digest) / "node_modules" / ".bin" / "acpx").unlink()

    result = _install(bundle, fake_npm.prefix)

    assert result.changed is True
    assert len(fake_npm.ci_calls()) == 2
    assert _run_bin(fake_npm.prefix, "acpx") == "acpx 0.3.0"
    assert sorted(path.name for path in layout.tool_root.iterdir()) == sorted(
        ["current", bundle.digest]
    )


def test_transient_npm_failures_retry_with_bounded_backoff(
    fake_npm: _FakeNpm, tmp_path: pathlib.Path, recorded_sleeps: list[float]
) -> None:
    """Network failures are retried with exponential backoff until `npm ci` succeeds."""
    bundle = _write_bundle(tmp_path / "assets")
    fake_npm.fail_next("ECONNRESET", "E503")

    _install(bundle, fake_npm.prefix)

    assert len(fake_npm.ci_calls()) == 3
    assert recorded_sleeps == [5, 15]


def test_resolution_errors_fail_immediately_and_keep_previous_state(
    fake_npm: _FakeNpm, tmp_path: pathlib.Path, recorded_sleeps: list[float]
) -> None:
    """ETARGET is deterministic: no retry, no cache clean, and the staging tree is discarded."""
    bundle = _write_bundle(tmp_path / "assets")
    fake_npm.fail_next("ETARGET")

    with pytest.raises(NodeToolsError, match="ETARGET"):
        _install(bundle, fake_npm.prefix)

    assert fake_npm.ci_calls() == [["ci", "--omit=dev", "--no-audit", "--no-fund"]]
    assert all(call[:1] != ["cache"] for call in fake_npm.calls())
    assert recorded_sleeps == []
    layout = NodeToolsLayout(fake_npm.prefix)
    assert list(layout.tool_root.iterdir()) == []
    assert not layout.bin_link("openclaw").exists()


def test_transient_retry_budget_is_bounded(
    fake_npm: _FakeNpm, tmp_path: pathlib.Path, recorded_sleeps: list[float]
) -> None:
    """Persistent network failures stop after the attempt budget."""
    bundle = _write_bundle(tmp_path / "assets")
    fake_npm.fail_next(*["ETIMEDOUT"] * node_tools.NPM_CI_MAX_ATTEMPTS)

    with pytest.raises(NodeToolsError, match="after 4 attempts"):
        _install(bundle, fake_npm.prefix)

    assert len(fake_npm.ci_calls()) == node_tools.NPM_CI_MAX_ATTEMPTS
    assert recorded_sleeps == [5, 15, 45]


@pytest.mark.parametrize(
    ("output", "transient"),
    [
        ("npm error code ECONNRESET\nnpm error errno ECONNRESET", True),
        ("npm error code ETIMEDOUT", True),
        ("npm error code EAI_AGAIN", True),
        ("npm error code E503", True),
        ("npm ERR! code E502", True),
        ("npm error code TAR_BAD_ARCHIVE", True),
        ("npm error code ETARGET\nnpm error notarget No matching version", False),
        ("npm error code ETARGET\nnpm error errno ECONNRESET", False),
        ("npm error code E404", False),
        ("npm error code EINTEGRITY", False),
        ("npm error code EUSAGE", False),
        ("npm error code 1\nnpm error command failed", False),
        ("something exploded", False),
    ],
)
def test_npm_failure_classification(output: str, transient: bool) -> None:
    """Only network/tarball codes are retryable; any resolution code disqualifies a retry."""
    assert is_transient_npm_failure(output) is transient


def test_retry_delay_is_exponential_and_capped() -> None:
    assert [npm_ci_retry_delay_seconds(attempt) for attempt in range(1, 6)] == [5, 15, 45, 60, 60]


class _CommandRecorder:
    """Stand-in for `run_command` that records argv and returns canned stdout."""

    def __init__(self, stdout: str = "") -> None:
        self.commands: list[list[str]] = []
        self._stdout = stdout

    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
        timeout_seconds: int = 30,
        check: bool = False,
    ) -> ExecResult:
        del cwd, timeout_seconds, check
        self.commands.append([str(part) for part in command])
        return ExecResult(
            argv=tuple(command), returncode=0, stdout=self._stdout, stderr="", duration_ms=1
        )


def test_sudo_mode_keeps_the_historical_privilege_rule(
    test_context: TestContext, tmp_path: pathlib.Path
) -> None:
    """Linux resolves and writes the global prefix through sudo, using `npm ci`, not `-g`."""
    recorder = _CommandRecorder(stdout="/usr/local\n")
    test_context.patch.patch_object(node_tools, "run_command", new=recorder)
    writer = SudoPrefixWriter()

    assert resolve_npm_global_prefix(command_prefix=writer.command_prefix) == pathlib.Path(
        "/usr/local"
    )
    run_npm_ci(tmp_path, command_prefix=writer.command_prefix)
    writer.replace_symlink(pathlib.Path("/usr/local/bin/openclaw"), "../lib/x")
    writer.rename(pathlib.Path("/usr/local/lib/a"), pathlib.Path("/usr/local/lib/b"))
    writer.remove(pathlib.Path("/usr/local/lib/node_modules/openclaw"))

    temporary = f"/usr/local/bin/.openclaw.strongclaw-tmp-{os.getpid()}"
    assert recorder.commands == [
        ["sudo", "npm", "prefix", "-g"],
        ["sudo", "npm", "ci", "--omit=dev", "--no-audit", "--no-fund"],
        ["sudo", "ln", "-sfn", "../lib/x", temporary],
        ["sudo", "mv", "-T", temporary, "/usr/local/bin/openclaw"],
        ["sudo", "mv", "-T", "/usr/local/lib/a", "/usr/local/lib/b"],
        ["sudo", "rm", "-rf", "/usr/local/lib/node_modules/openclaw"],
    ]


def test_ensure_node_tools_installed_uses_the_committed_lock(
    fake_npm: _FakeNpm, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bootstrap entrypoint installs the repo lock under `npm prefix -g`."""
    result = ensure_node_tools_installed(repo_root=REPO_ROOT, use_sudo=False)
    bundle = load_node_tools_bundle(REPO_ROOT / "platform" / "node-tools")

    assert result.prefix == fake_npm.prefix
    assert result.release_dir == NodeToolsLayout(fake_npm.prefix).release_dir(bundle.digest)
    assert {path.name for path in result.bin_paths} == {"openclaw", "acpx"}
    assert _run_bin(fake_npm.prefix, "openclaw") == "openclaw 2026.3.13"
    assert "is not on PATH" in capsys.readouterr().err


def test_path_shadowing_is_reported(
    test_context: TestContext,
    fake_npm: _FakeNpm,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A different `openclaw` earlier on PATH is surfaced instead of silently winning."""
    bundle = _write_bundle(tmp_path / "assets")
    shadow_dir = tmp_path / "shadow"
    shadow_dir.mkdir()
    (shadow_dir / "openclaw").write_text("#!/bin/sh\n", encoding="utf-8")
    (shadow_dir / "openclaw").chmod(0o755)
    test_context.env.prepend_path(fake_npm.prefix / "bin")
    test_context.env.prepend_path(shadow_dir)

    _install(bundle, fake_npm.prefix)

    warnings = capsys.readouterr().err
    assert f"shadowing {fake_npm.prefix / 'bin' / 'openclaw'}" in warnings
    assert "acpx" not in warnings


def _mutate_lock(asset_dir: pathlib.Path, key: str, field: str, value: LockFieldValue) -> None:
    lock_path = asset_dir / "package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    entry = lock["packages"][key]
    if value is None:
        entry.pop(field)
    else:
        entry[field] = value
    lock_path.write_text(json.dumps(lock), encoding="utf-8")


@pytest.mark.parametrize(
    ("key", "field", "value", "message"),
    [
        ("node_modules/acpx", "integrity", None, "sha512 `integrity`"),
        ("node_modules/acpx", "resolved", None, "missing `resolved`"),
        ("node_modules/libsignal", "resolved", "git+https://github.com/x/y.git", "unpinned"),
        ("node_modules/acpx", "link", True, "local link"),
        ("node_modules/acpx", "version", "0.3.1", "locked at '0.3.1'"),
        ("", "dependencies", {"acpx": "0.3.0"}, "do not match the manifest"),
    ],
)
def test_bundle_rejects_non_reproducible_locks(
    tmp_path: pathlib.Path, key: str, field: str, value: LockFieldValue, message: str
) -> None:
    """Every installed entry must come from an immutable, integrity-checked source."""
    asset_dir = tmp_path / "assets"
    _write_bundle(asset_dir)
    _mutate_lock(asset_dir, key, field, value)

    with pytest.raises(NodeToolsError, match=message):
        load_node_tools_bundle(asset_dir)


def test_bundle_rejects_range_pins(tmp_path: pathlib.Path) -> None:
    asset_dir = tmp_path / "assets"
    _write_bundle(asset_dir)
    manifest_path = asset_dir / "package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dependencies"]["openclaw"] = "^2026.3.13"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(NodeToolsError, match="exact version"):
        load_node_tools_bundle(asset_dir)


def test_bundle_digest_tracks_the_lock_bytes(tmp_path: pathlib.Path) -> None:
    first = _write_bundle(tmp_path / "a")
    same = _write_bundle(tmp_path / "b")
    bumped = _write_bundle(tmp_path / "c", openclaw="2026.3.14")

    assert first.digest == same.digest
    assert first.digest != bumped.digest
    assert len(first.digest) == 16
