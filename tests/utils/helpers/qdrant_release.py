"""Checksum-pinned Qdrant release binaries and a managed process for storage-upgrade tests."""

from __future__ import annotations

import hashlib
import io
import os
import platform
import socket
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path
from types import TracebackType

import requests

_RELEASE_URL = "https://github.com/qdrant/qdrant/releases/download/{release}/qdrant-{target}.tar.gz"
# sha256 of each release asset, from the GitHub release metadata.
RELEASE_SHA256: dict[tuple[str, str], str] = {
    ("v1.15.5", "x86_64-unknown-linux-gnu"): (
        "56b41911cc0f891ef47d1a4c5cb1f62a423db654ab3694f83cfd37643fea082d"
    ),
    ("v1.15.5", "x86_64-apple-darwin"): (
        "b25f5512f2b696bae84752ff50752a77eec2ce6955e00847816e05c6344d6af9"
    ),
    ("v1.15.5", "aarch64-apple-darwin"): (
        "e712f362e1e9aff18bb062f15c05fa384282ededc7d49f1935792cdc64e25222"
    ),
    ("v1.20.0", "x86_64-unknown-linux-gnu"): (
        "2361f3df0dedfef31f5444f9f15bd15d6b4a6cba6ad952daecc5fbe3867c5ee6"
    ),
    ("v1.20.0", "x86_64-apple-darwin"): (
        "94a2e6f6b8ef3a3b5756a11172fd66554ac753e0a71c1d8d8b5349356c084f4d"
    ),
    ("v1.20.0", "aarch64-apple-darwin"): (
        "531904e822050790bb8c1da6705f801ee48b91eb4c229d0d605ff06f6331b20a"
    ),
}


def host_target() -> str | None:
    """Return this host's Qdrant release target triple, or None when unsupported."""
    system, machine = platform.system(), platform.machine().lower()
    if system == "Linux" and machine in ("x86_64", "amd64"):
        return "x86_64-unknown-linux-gnu"
    if system == "Darwin" and machine == "x86_64":
        return "x86_64-apple-darwin"
    if system == "Darwin" and machine in ("arm64", "aarch64"):
        return "aarch64-apple-darwin"
    return None


def fetch_release_binary(release: str, *, target: str, cache_dir: Path) -> Path:
    """Download, verify, and unpack one Qdrant release binary, reusing a verified cache."""
    expected = RELEASE_SHA256[(release, target)]
    binary = cache_dir / release / "qdrant"
    if binary.is_file():
        return binary
    url = _RELEASE_URL.format(release=release, target=target)
    with urllib.request.urlopen(url, timeout=300) as response:
        archive = response.read()
    digest = hashlib.sha256(archive).hexdigest()
    if digest != expected:
        raise RuntimeError(f"{url} sha256 {digest} does not match pinned {expected}")
    binary.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        member = bundle.getmember("qdrant")
        extracted = bundle.extractfile(member)
        if extracted is None:
            raise RuntimeError(f"{url} has no qdrant binary")
        binary.write_bytes(extracted.read())
    binary.chmod(0o755)
    return binary


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class QdrantProcess:
    """Run one Qdrant binary against a storage directory on ephemeral local ports."""

    def __init__(self, binary: Path, *, storage_dir: Path, log_path: Path) -> None:
        self.binary = binary
        self.storage_dir = storage_dir
        self.log_path = log_path
        self.http_port = _free_port()
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def url(self) -> str:
        """Return the HTTP base URL."""
        return f"http://127.0.0.1:{self.http_port}"

    def __enter__(self) -> QdrantProcess:
        env = {
            **os.environ,
            "QDRANT__STORAGE__STORAGE_PATH": str(self.storage_dir),
            "QDRANT__STORAGE__SNAPSHOTS_PATH": str(self.storage_dir.parent / "qdrant-snapshots"),
            "QDRANT__SERVICE__HTTP_PORT": str(self.http_port),
            "QDRANT__SERVICE__GRPC_PORT": str(_free_port()),
            "QDRANT__TELEMETRY_DISABLED": "true",
        }
        with self.log_path.open("ab") as log:
            self._process = subprocess.Popen(
                [str(self.binary)],
                cwd=self.storage_dir.parent,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        return self

    def wait_healthy(self, timeout_seconds: float = 60.0) -> bool:
        """Return True once /healthz answers; False if the process exits or times out."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._process is None or self._process.poll() is not None:
                return False
            try:
                if requests.get(f"{self.url}/healthz", timeout=1.0).ok:
                    return True
            except requests.RequestException:
                pass
            time.sleep(0.25)
        return False

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._process is None or self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=20)
