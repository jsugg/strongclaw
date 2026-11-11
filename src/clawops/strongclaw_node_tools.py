"""Install the locked OpenClaw/ACPX CLI tree and expose it on npm's global bin directory.

The committed ``platform/node-tools`` manifest and lock are installed with ``npm ci`` into an
immutable release directory keyed by the lock digest, next to npm's own global tree::

    <npm prefix -g>/lib/strongclaw-node-tools/<digest>/      release (``npm ci`` output)
    <npm prefix -g>/lib/strongclaw-node-tools/current        -> <digest>
    <npm prefix -g>/bin/<bin>  -> ../lib/strongclaw-node-tools/current/node_modules/.bin/<bin>
    <npm prefix -g>/lib/node_modules/<pkg>  -> ../strongclaw-node-tools/current/node_modules/<pkg>

The bins land exactly where ``npm install -g`` used to put them, so shells, service units, and
every ``openclaw``/``acpx`` call site keep resolving them through the same PATH entry. The
``lib/node_modules/<pkg>`` compat links keep absolute-path consumers of the legacy global layout
(for example the memory plugin's ``openclaw/dist/extensionAPI.js`` fallbacks) working. A release
is smoke-tested before ``current`` is switched with an atomic rename, so the previous install
keeps serving until the new one passes.

npm sees the compat links as linked global packages. ``npm uninstall -g <pkg>`` therefore removes
that package's compat link and bin link (never the managed release), and ``npm install -g <pkg>``
replaces them with a floating install; both are explicit user overrides that the next
``clawops bootstrap`` reconciles back to the lock.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from typing import Final, Literal, Protocol, cast

from clawops.platform_compat import parse_node_tool_pins
from clawops.runtime_assets import resolve_asset_path
from clawops.strongclaw_runtime import CommandError, ExecResult, run_command

NODE_TOOLS_ASSET_DIR: Final[pathlib.Path] = pathlib.Path("platform/node-tools")
MANIFEST_FILE_NAME: Final[str] = "package.json"
LOCK_FILE_NAME: Final[str] = "package-lock.json"
TOOL_ROOT_NAME: Final[str] = "strongclaw-node-tools"
CURRENT_LINK_NAME: Final[str] = "current"
NPM_CI_ARGUMENTS: Final[tuple[str, ...]] = ("ci", "--omit=dev", "--no-audit", "--no-fund")
NPM_CI_MAX_ATTEMPTS: Final[int] = 4
NPM_CI_RETRY_BASE_DELAY_SECONDS: Final[int] = 5
NPM_CI_RETRY_BACKOFF_FACTOR: Final[int] = 3
NPM_CI_RETRY_MAX_DELAY_SECONDS: Final[int] = 60
# Network and truncated-tarball failures only; resolution errors such as ETARGET, E404,
# EINTEGRITY, or EUSAGE (lock/manifest drift) are deterministic and must fail immediately.
TRANSIENT_NPM_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        "E429",
        "EAI_AGAIN",
        "ECONNREFUSED",
        "ECONNRESET",
        "EHOSTUNREACH",
        "ENETUNREACH",
        "EPIPE",
        "ERR_SOCKET_TIMEOUT",
        "ERR_STREAM_PREMATURE_CLOSE",
        "ESOCKETTIMEDOUT",
        "ETIMEDOUT",
        "TAR_BAD_ARCHIVE",
        "Z_BUF_ERROR",
    }
)
_RELEASE_DIGEST_LENGTH: Final[int] = 16
_RELEASE_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{16}$")
_STAGING_PREFIX: Final[str] = ".staging-"
_RETIRED_PREFIX: Final[str] = ".retired-"
_LINK_TMP_MARKER: Final[str] = ".strongclaw-tmp-"
_NPM_ERROR_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^npm (?:error|ERR!) (?:code|errno) (\S+)\s*$", re.MULTILINE
)
_HTTP_SERVER_ERROR_PATTERN: Final[re.Pattern[str]] = re.compile(r"^E5\d\d$")
_GIT_COMMIT_SUFFIX_PATTERN: Final[re.Pattern[str]] = re.compile(r"#[0-9a-f]{40}$")
_BIN_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_NPM_CI_TIMEOUT_SECONDS: Final[int] = 1800
_SMOKE_TIMEOUT_SECONDS: Final[int] = 60
_PRIVILEGED_COMMAND_TIMEOUT_SECONDS: Final[int] = 600

BinLinkState = Literal["absent", "managed", "replaceable"]


class NodeToolsError(CommandError):
    """Raised when the locked node-tools bundle is invalid or cannot be installed."""


@dataclasses.dataclass(frozen=True, slots=True)
class NodeToolPackage:
    """One directly pinned node-tools package and the bins it exposes."""

    name: str
    version: str
    bins: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class NodeToolsBundle:
    """Validated manifest plus lock for the node-tools tree."""

    manifest_path: pathlib.Path
    lock_path: pathlib.Path
    digest: str
    packages: tuple[NodeToolPackage, ...]

    @property
    def bin_names(self) -> tuple[str, ...]:
        """Return every bin name exposed by the pinned packages."""
        return tuple(bin_name for package in self.packages for bin_name in package.bins)


@dataclasses.dataclass(frozen=True, slots=True)
class NodeToolsLayout:
    """Filesystem layout under one npm global prefix."""

    prefix: pathlib.Path

    @property
    def bin_dir(self) -> pathlib.Path:
        """Return npm's global bin directory (POSIX layout)."""
        return self.prefix / "bin"

    @property
    def global_node_modules(self) -> pathlib.Path:
        """Return npm's global package directory, home of legacy installs."""
        return self.prefix / "lib" / "node_modules"

    @property
    def tool_root(self) -> pathlib.Path:
        """Return the directory holding managed releases."""
        return self.prefix / "lib" / TOOL_ROOT_NAME

    @property
    def current_link(self) -> pathlib.Path:
        """Return the symlink naming the active release."""
        return self.tool_root / CURRENT_LINK_NAME

    def release_dir(self, digest: str) -> pathlib.Path:
        """Return the release directory for one lock digest."""
        return self.tool_root / digest

    def bin_link(self, bin_name: str) -> pathlib.Path:
        """Return the published bin path for one bin name."""
        return self.bin_dir / bin_name

    def bin_link_target(self, bin_name: str) -> str:
        """Return the relative symlink target routing one bin through `current`."""
        target = self.current_link / "node_modules" / ".bin" / bin_name
        return os.path.relpath(target, self.bin_dir)

    def compat_package_link(self, package_name: str) -> pathlib.Path:
        """Return the legacy `npm install -g` package path kept as a compat link."""
        return self.global_node_modules / package_name

    def compat_package_target(self, package_name: str) -> str:
        """Return the relative compat-link target routing one package through `current`."""
        link = self.compat_package_link(package_name)
        target = self.current_link / "node_modules" / package_name
        return os.path.relpath(target, link.parent)


@dataclasses.dataclass(frozen=True, slots=True)
class NodeToolsInstallResult:
    """Outcome of one node-tools reconciliation."""

    prefix: pathlib.Path
    release_dir: pathlib.Path
    bin_paths: tuple[pathlib.Path, ...]
    changed: bool


class PrefixWriter(Protocol):
    """Mutating filesystem operations under the npm global prefix."""

    @property
    def command_prefix(self) -> tuple[str, ...]:
        """Return the argv prefix used for npm commands that write under the prefix."""
        ...

    def make_dirs(self, path: pathlib.Path) -> None:
        """Create a directory and its parents."""
        ...

    def copy_file(self, source: pathlib.Path, target: pathlib.Path) -> None:
        """Copy one regular file."""
        ...

    def rename(self, source: pathlib.Path, target: pathlib.Path) -> None:
        """Atomically rename *source* to a non-existent *target*."""
        ...

    def replace_symlink(self, link: pathlib.Path, target: str) -> None:
        """Atomically create or replace *link* so it points at *target*."""
        ...

    def remove(self, path: pathlib.Path) -> None:
        """Remove a file, symlink, or directory tree; missing paths are ignored."""
        ...


class LocalPrefixWriter:
    """Write under a prefix owned by the invoking user."""

    @property
    def command_prefix(self) -> tuple[str, ...]:
        """Return an empty argv prefix."""
        return ()

    def make_dirs(self, path: pathlib.Path) -> None:
        """Create a directory and its parents."""
        path.mkdir(parents=True, exist_ok=True)

    def copy_file(self, source: pathlib.Path, target: pathlib.Path) -> None:
        """Copy one regular file."""
        shutil.copyfile(source, target)

    def rename(self, source: pathlib.Path, target: pathlib.Path) -> None:
        """Atomically rename *source* to a non-existent *target*."""
        os.rename(source, target)

    def replace_symlink(self, link: pathlib.Path, target: str) -> None:
        """Atomically create or replace *link* via a temporary sibling symlink."""
        temporary = _temporary_link_path(link)
        self.remove(temporary)
        os.symlink(target, temporary)
        os.replace(temporary, link)

    def remove(self, path: pathlib.Path) -> None:
        """Remove a file, symlink, or directory tree; missing paths are ignored."""
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)


class SudoPrefixWriter:
    """Write under a root-owned prefix, mirroring the historical `sudo npm install -g`."""

    @property
    def command_prefix(self) -> tuple[str, ...]:
        """Return the sudo argv prefix."""
        return ("sudo",)

    def _run(self, arguments: Sequence[str]) -> None:
        run_command(
            ["sudo", *arguments],
            timeout_seconds=_PRIVILEGED_COMMAND_TIMEOUT_SECONDS,
            check=True,
        )

    def make_dirs(self, path: pathlib.Path) -> None:
        """Create a directory and its parents."""
        self._run(["mkdir", "-p", str(path)])

    def copy_file(self, source: pathlib.Path, target: pathlib.Path) -> None:
        """Copy one regular file."""
        self._run(["cp", str(source), str(target)])

    def rename(self, source: pathlib.Path, target: pathlib.Path) -> None:
        """Atomically rename *source* to a non-existent *target* (GNU `mv -T`)."""
        self._run(["mv", "-T", str(source), str(target)])

    def replace_symlink(self, link: pathlib.Path, target: str) -> None:
        """Atomically create or replace *link* via a temporary sibling symlink."""
        temporary = _temporary_link_path(link)
        self._run(["ln", "-sfn", target, str(temporary)])
        self._run(["mv", "-T", str(temporary), str(link)])

    def remove(self, path: pathlib.Path) -> None:
        """Remove a file, symlink, or directory tree; missing paths are ignored."""
        self._run(["rm", "-rf", str(path)])


def _temporary_link_path(link: pathlib.Path) -> pathlib.Path:
    """Return the per-process temporary sibling used for atomic symlink replacement."""
    return link.with_name(f".{link.name}{_LINK_TMP_MARKER}{os.getpid()}")


def _read_json(path: pathlib.Path, raw: bytes) -> object:
    """Decode one JSON document read from *path*."""
    try:
        return cast(object, json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise NodeToolsError(f"{path}: invalid JSON: {err}") from err


def _require_mapping(value: object, *, context: str) -> dict[str, object]:
    """Return *value* as a JSON object or raise."""
    if not isinstance(value, dict):
        raise NodeToolsError(f"{context} must be a JSON object")
    return cast(dict[str, object], value)


def _validate_lock_entry(key: str, entry: dict[str, object], *, source: str) -> None:
    """Require every installed lock entry to be fetched from an immutable source."""
    if entry.get("link") is True:
        raise NodeToolsError(f"{source}: {key} is a local link; the lock must be self-contained")
    resolved = entry.get("resolved")
    if not isinstance(resolved, str) or not resolved:
        raise NodeToolsError(f"{source}: {key} is missing `resolved`")
    if resolved.startswith("https://"):
        integrity = entry.get("integrity")
        if not isinstance(integrity, str) or not integrity.startswith("sha512-"):
            raise NodeToolsError(f"{source}: {key} is missing a sha512 `integrity`")
        return
    # npm cannot record integrity for git dependencies; a full commit SHA is the immutable pin.
    if resolved.startswith("git+") and _GIT_COMMIT_SUFFIX_PATTERN.search(resolved) is not None:
        return
    raise NodeToolsError(f"{source}: {key} resolves from an unpinned source: {resolved}")


def _package_bins(name: str, entry: dict[str, object], *, source: str) -> tuple[str, ...]:
    """Return the validated bin names a directly pinned package declares."""
    raw_bins = entry.get("bin")
    if not isinstance(raw_bins, dict) or not raw_bins:
        raise NodeToolsError(f"{source}: {name} declares no executables")
    bins = sorted(str(bin_name) for bin_name in cast(dict[object, object], raw_bins))
    for bin_name in bins:
        if _BIN_NAME_PATTERN.fullmatch(bin_name) is None:
            raise NodeToolsError(f"{source}: {name} declares an unsafe bin name {bin_name!r}")
    return tuple(bins)


def parse_node_tools_lock(
    payload: object,
    *,
    pins: Mapping[str, str],
    source: str,
) -> tuple[NodeToolPackage, ...]:
    """Validate a package-lock payload against the manifest pins."""
    lock = _require_mapping(payload, context=source)
    lockfile_version = lock.get("lockfileVersion")
    if not isinstance(lockfile_version, int) or lockfile_version < 3:
        raise NodeToolsError(f"{source}: lockfileVersion 3 is required, got {lockfile_version!r}")
    packages = _require_mapping(lock.get("packages"), context=f"{source}: `packages`")
    root = _require_mapping(packages.get(""), context=f"{source}: root package entry")
    if root.get("dependencies") != dict(pins):
        raise NodeToolsError(
            f"{source}: lock root dependencies do not match the manifest; regenerate it with "
            "tests/scripts/update_node_tools_lock.py"
        )
    for key, raw_entry in packages.items():
        if key:
            _validate_lock_entry(key, _require_mapping(raw_entry, context=key), source=source)
    pinned: list[NodeToolPackage] = []
    for name, version in pins.items():
        entry = _require_mapping(
            packages.get(f"node_modules/{name}"), context=f"{source}: node_modules/{name}"
        )
        if entry.get("version") != version:
            raise NodeToolsError(
                f"{source}: {name} is locked at {entry.get('version')!r}, manifest pins {version}"
            )
        pinned.append(
            NodeToolPackage(
                name=name, version=version, bins=_package_bins(name, entry, source=source)
            )
        )
    return tuple(pinned)


def resolve_node_tools_dir(repo_root: pathlib.Path | None = None) -> pathlib.Path:
    """Return the source/packaged directory holding the node-tools manifest and lock."""
    return resolve_asset_path(NODE_TOOLS_ASSET_DIR, repo_root=repo_root)


def load_node_tools_bundle(asset_dir: pathlib.Path) -> NodeToolsBundle:
    """Load and validate the node-tools manifest and lock from *asset_dir*."""
    manifest_path = asset_dir / MANIFEST_FILE_NAME
    lock_path = asset_dir / LOCK_FILE_NAME
    try:
        manifest_bytes = manifest_path.read_bytes()
        lock_bytes = lock_path.read_bytes()
    except OSError as err:
        raise NodeToolsError(f"node-tools bundle is unreadable: {err}") from err
    try:
        pins = parse_node_tool_pins(
            _read_json(manifest_path, manifest_bytes), source=str(manifest_path)
        )
    except ValueError as err:
        raise NodeToolsError(str(err)) from err
    packages = parse_node_tools_lock(
        _read_json(lock_path, lock_bytes), pins=pins, source=str(lock_path)
    )
    return NodeToolsBundle(
        manifest_path=manifest_path,
        lock_path=lock_path,
        digest=hashlib.sha256(lock_bytes).hexdigest()[:_RELEASE_DIGEST_LENGTH],
        packages=packages,
    )


def npm_error_codes(output: str) -> tuple[str, ...]:
    """Return the `npm error code|errno` values reported in *output*."""
    return tuple(dict.fromkeys(_NPM_ERROR_CODE_PATTERN.findall(output)))


def is_transient_npm_failure(output: str) -> bool:
    """Return whether every npm error code in *output* is a retryable network failure."""
    codes = npm_error_codes(output)
    return bool(codes) and all(
        code in TRANSIENT_NPM_ERROR_CODES or _HTTP_SERVER_ERROR_PATTERN.fullmatch(code) is not None
        for code in codes
    )


def npm_ci_retry_delay_seconds(attempt: int) -> int:
    """Return the bounded exponential backoff before retry number *attempt* (1-based)."""
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    backoff = int(NPM_CI_RETRY_BASE_DELAY_SECONDS * NPM_CI_RETRY_BACKOFF_FACTOR ** (attempt - 1))
    return min(NPM_CI_RETRY_MAX_DELAY_SECONDS, backoff)


def _echo_result(result: ExecResult) -> None:
    """Replay captured npm output so logs read as if the command had streamed."""
    if result.stdout:
        sys.stdout.write(result.stdout)
        sys.stdout.flush()
    if result.stderr:
        sys.stderr.write(result.stderr)
        sys.stderr.flush()


def run_npm_ci(
    cwd: pathlib.Path,
    *,
    command_prefix: Sequence[str] = (),
    extra_arguments: Sequence[str] = (),
) -> None:
    """Run `npm ci` in *cwd*, retrying only transient network/tarball failures."""
    command = [*command_prefix, "npm", *NPM_CI_ARGUMENTS, *extra_arguments]
    for attempt in range(1, NPM_CI_MAX_ATTEMPTS + 1):
        result = run_command(command, cwd=cwd, timeout_seconds=_NPM_CI_TIMEOUT_SECONDS)
        _echo_result(result)
        if result.ok:
            return
        output = f"{result.stdout}\n{result.stderr}"
        codes = ", ".join(npm_error_codes(output)) or "no npm error code"
        if result.timed_out or not is_transient_npm_failure(output):
            reason = "timed out" if result.timed_out else codes
            raise NodeToolsError(f"npm ci failed in {cwd} ({reason})", result=result)
        if attempt == NPM_CI_MAX_ATTEMPTS:
            raise NodeToolsError(
                f"npm ci failed in {cwd} after {attempt} attempts ({codes})", result=result
            )
        delay_seconds = npm_ci_retry_delay_seconds(attempt)
        print(
            f"npm ci hit a transient registry failure ({codes}); retrying in {delay_seconds}s "
            f"(attempt {attempt + 1}/{NPM_CI_MAX_ATTEMPTS})",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(delay_seconds)


def _reports_version(output: str, version: str) -> bool:
    """Return whether *output* names *version* as a standalone token."""
    pattern = rf"(?<![0-9.]){re.escape(version)}(?![0-9A-Za-z.+-])"
    return re.search(pattern, output) is not None


def smoke_test_node_tools(bin_dir: pathlib.Path, bundle: NodeToolsBundle) -> None:
    """Require every bin in *bin_dir* to run and report its pinned package version."""
    for package in bundle.packages:
        for bin_name in package.bins:
            executable = bin_dir / bin_name
            result = run_command(
                [str(executable), "--version"], timeout_seconds=_SMOKE_TIMEOUT_SECONDS
            )
            if not result.ok or not _reports_version(result.stdout, package.version):
                detail = (result.stdout or result.stderr).strip() or "no output"
                raise NodeToolsError(
                    f"{executable} --version smoke test failed: expected {package.name} "
                    f"{package.version}, got: {detail}",
                    result=result,
                )


def stage_node_tools_tree(
    bundle: NodeToolsBundle,
    target_dir: pathlib.Path,
    writer: PrefixWriter,
    *,
    extra_npm_arguments: Sequence[str] = (),
) -> None:
    """Copy the manifest and lock into *target_dir* and run `npm ci` there."""
    writer.make_dirs(target_dir)
    writer.copy_file(bundle.manifest_path, target_dir / MANIFEST_FILE_NAME)
    writer.copy_file(bundle.lock_path, target_dir / LOCK_FILE_NAME)
    run_npm_ci(
        target_dir,
        command_prefix=writer.command_prefix,
        extra_arguments=extra_npm_arguments,
    )


def prefetch_node_tools_packages(*, repo_root: pathlib.Path | None = None) -> None:
    """Populate the npm cache with the locked tarballs without activating anything."""
    bundle = load_node_tools_bundle(resolve_node_tools_dir(repo_root))
    with tempfile.TemporaryDirectory(prefix="strongclaw-node-tools-prefetch.") as temp_dir:
        stage_node_tools_tree(
            bundle,
            pathlib.Path(temp_dir),
            LocalPrefixWriter(),
            extra_npm_arguments=("--ignore-scripts",),
        )


def resolve_npm_global_prefix(*, command_prefix: Sequence[str] = ()) -> pathlib.Path:
    """Return the prefix `npm install -g` writes to for the given privilege context."""
    command = [*command_prefix, "npm", "prefix", "-g"]
    result = run_command(command, timeout_seconds=60)
    lines = result.stdout.strip().splitlines()
    if not result.ok or not lines:
        detail = result.stderr.strip() or "no output"
        raise NodeToolsError(f"could not resolve the npm global prefix: {detail}", result=result)
    prefix = pathlib.Path(lines[-1].strip())
    if not prefix.is_absolute():
        raise NodeToolsError(f"npm reported a non-absolute global prefix: {prefix}")
    return prefix


def _is_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    """Return whether *path* equals or sits below *root* (lexically)."""
    return path == root or root in path.parents


class NodeToolsInstaller:
    """Reconcile the npm global prefix with one validated node-tools bundle."""

    def __init__(
        self,
        bundle: NodeToolsBundle,
        layout: NodeToolsLayout,
        writer: PrefixWriter,
    ) -> None:
        self._bundle = bundle
        self._layout = layout
        self._writer = writer

    def ensure(self) -> NodeToolsInstallResult:
        """Install, activate, and link the locked tree; a matching healthy install is a no-op."""
        layout = self._layout
        digest = self._bundle.digest
        release_dir = layout.release_dir(digest)
        print(
            f"node tools: prefix={layout.prefix} release={digest} "
            f"bins={','.join(self._bundle.bin_names)}",
            flush=True,
        )
        pending_links = [
            bin_name
            for bin_name in self._bundle.bin_names
            if self._bin_link_state(bin_name) != "managed"
        ]
        changed = False
        if not self._release_is_healthy(release_dir):
            self._install_release(release_dir)
            changed = True
        if self._active_release_name() != digest:
            self._writer.replace_symlink(layout.current_link, digest)
            changed = True
        if pending_links:
            self._writer.make_dirs(layout.bin_dir)
        for bin_name in pending_links:
            self._writer.replace_symlink(
                layout.bin_link(bin_name), layout.bin_link_target(bin_name)
            )
            changed = True
        if self._link_compat_packages():
            changed = True
        if changed:
            smoke_test_node_tools(layout.bin_dir, self._bundle)
        self._prune_inactive(keep=digest)
        self._warn_on_path_mismatch()
        return NodeToolsInstallResult(
            prefix=layout.prefix,
            release_dir=release_dir,
            bin_paths=tuple(layout.bin_link(name) for name in self._bundle.bin_names),
            changed=changed,
        )

    def _bin_link_state(self, bin_name: str) -> BinLinkState:
        """Classify the current occupant of one bin path; foreign files are a hard error."""
        link = self._layout.bin_link(bin_name)
        if not link.is_symlink():
            if link.exists():
                raise NodeToolsError(self._collision_message(link, "a non-symlink file"))
            return "absent"
        target = os.readlink(link)
        if target == self._layout.bin_link_target(bin_name):
            return "managed"
        resolved = pathlib.Path(os.path.normpath(self._layout.bin_dir / target))
        owners = [
            self._layout.global_node_modules / package.name for package in self._bundle.packages
        ]
        if _is_within(resolved, self._layout.tool_root) or any(
            _is_within(resolved, owner) for owner in owners
        ):
            return "replaceable"
        raise NodeToolsError(self._collision_message(link, f"a symlink to {target}"))

    def _collision_message(self, link: pathlib.Path, occupant: str) -> str:
        return (
            f"refusing to replace {link}: it is {occupant} not installed by npm for "
            f"{', '.join(package.name for package in self._bundle.packages)}. "
            "Remove it, then re-run `clawops bootstrap`."
        )

    def _release_is_healthy(self, release_dir: pathlib.Path) -> bool:
        """Return whether a completed release exists and passes the smoke test."""
        if not release_dir.is_dir() or release_dir.is_symlink():
            return False
        try:
            smoke_test_node_tools(release_dir / "node_modules" / ".bin", self._bundle)
        except NodeToolsError as err:
            print(f"node tools: reinstalling unhealthy release {release_dir}: {err}", flush=True)
            return False
        return True

    def _install_release(self, release_dir: pathlib.Path) -> None:
        """Build a release in staging and move it into place only after it passes."""
        tool_root = self._layout.tool_root
        token = f"{self._bundle.digest}-{os.getpid()}"
        staging_dir = tool_root / f"{_STAGING_PREFIX}{token}"
        self._writer.remove(staging_dir)
        try:
            stage_node_tools_tree(self._bundle, staging_dir, self._writer)
            smoke_test_node_tools(staging_dir / "node_modules" / ".bin", self._bundle)
        except CommandError:
            self._writer.remove(staging_dir)
            raise
        if release_dir.exists() or release_dir.is_symlink():
            retired_dir = tool_root / f"{_RETIRED_PREFIX}{token}"
            self._writer.remove(retired_dir)
            self._writer.rename(release_dir, retired_dir)
        self._writer.rename(staging_dir, release_dir)
        try:
            smoke_test_node_tools(release_dir / "node_modules" / ".bin", self._bundle)
        except CommandError:
            if self._active_release_name() != release_dir.name:
                self._writer.remove(release_dir)
            raise

    def _active_release_name(self) -> str | None:
        """Return the release name `current` points at, if any."""
        current_link = self._layout.current_link
        if not current_link.is_symlink():
            return None
        return os.readlink(current_link)

    def _link_compat_packages(self) -> bool:
        """Point each legacy global package path at the active release."""
        changed = False
        for package in self._bundle.packages:
            link = self._layout.compat_package_link(package.name)
            target = self._layout.compat_package_target(package.name)
            if link.is_symlink() and os.readlink(link) == target:
                continue
            self._writer.make_dirs(link.parent)
            if link.exists() and not link.is_symlink():
                # rename(2) cannot swap a directory for a symlink, so the old `npm install -g`
                # copy moves aside first; the bins already route through the managed tree.
                print(f"node tools: replacing legacy global package {link}", flush=True)
                retired = self._layout.tool_root / (
                    f"{_RETIRED_PREFIX}legacy-{package.name.replace('/', '+')}-{os.getpid()}"
                )
                self._writer.remove(retired)
                self._writer.rename(link, retired)
            self._writer.replace_symlink(link, target)
            changed = True
        return changed

    def _prune_inactive(self, *, keep: str) -> None:
        """Remove superseded releases plus leftover staging/retired/temporary entries."""
        tool_root = self._layout.tool_root
        if not tool_root.is_dir():
            return
        for entry in sorted(tool_root.iterdir()):
            name = entry.name
            if name in {CURRENT_LINK_NAME, keep}:
                continue
            if (
                _RELEASE_NAME_PATTERN.fullmatch(name) is not None
                or name.startswith((_STAGING_PREFIX, _RETIRED_PREFIX))
                or _LINK_TMP_MARKER in name
            ):
                self._writer.remove(entry)

    def _warn_on_path_mismatch(self) -> None:
        """Warn when PATH would not resolve a managed bin to its published link."""
        for bin_name in self._bundle.bin_names:
            expected = self._layout.bin_link(bin_name)
            found = shutil.which(bin_name)
            if found is None:
                print(
                    f"warning: {bin_name} is installed at {expected}, but {self._layout.bin_dir} "
                    "is not on PATH",
                    file=sys.stderr,
                )
            elif os.path.realpath(found) != os.path.realpath(expected):
                print(
                    f"warning: {bin_name} on PATH resolves to {found}, shadowing {expected}",
                    file=sys.stderr,
                )


def ensure_node_tools_installed(
    *,
    repo_root: pathlib.Path | None = None,
    use_sudo: bool,
) -> NodeToolsInstallResult:
    """Install the locked OpenClaw/ACPX tree behind npm's global bin directory."""
    bundle = load_node_tools_bundle(resolve_node_tools_dir(repo_root))
    writer: PrefixWriter = SudoPrefixWriter() if use_sudo else LocalPrefixWriter()
    layout = NodeToolsLayout(prefix=resolve_npm_global_prefix(command_prefix=writer.command_prefix))
    return NodeToolsInstaller(bundle, layout, writer).ensure()
