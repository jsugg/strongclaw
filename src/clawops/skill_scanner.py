"""Manifest-driven intake scanning for local OpenClaw skills and bundles."""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Final, Iterable, cast

from clawops.common import load_json, sha256_hex, write_json

SCAN_VERSION: Final[int] = 2
MANIFEST_VERSION: Final[int] = 1
SKILL_STAGES: Final[tuple[str, ...]] = ("quarantine", "reviewed", "approved")
TRANSITIONS: Final[dict[str, set[str]]] = {
    "scanned": {"quarantine", "reviewed"},
    "quarantined": {"reviewed"},
    "reviewed": {"quarantine", "approved"},
    "approved": {"reviewed"},
}

PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("critical", "shell exfil", re.compile(r"\b(curl|wget|Invoke-WebRequest)\b", re.IGNORECASE)),
    ("critical", "shell delete", re.compile(r"\brm\s+-rf\b", re.IGNORECASE)),
    ("high", "js child_process", re.compile(r"\bchild_process\b")),
    ("high", "js eval", re.compile(r"\beval\s*\(")),
    ("high", "python subprocess", re.compile(r"\bsubprocess\.(run|Popen|call)\b")),
    ("high", "install hook", re.compile(r'"(postinstall|preinstall|prepare)"\s*:', re.IGNORECASE)),
    ("high", "dynamic exec", re.compile(r"\b(exec|Function)\s*\(")),
    ("medium", "env read", re.compile(r"\b(process\.env|os\.environ)\b")),
    ("medium", "network client", re.compile(r"\b(requests\.(get|post)|fetch\s*\(|axios\.)")),
    ("medium", "base64 decode", re.compile(r"\bbase64\b", re.IGNORECASE)),
    ("medium", "shell pipeline", re.compile(r"\|\s*(sh|bash|zsh)\b", re.IGNORECASE)),
    ("medium", "broad traversal", re.compile(r"\b(rglob|glob|os\.walk|find\s+\.)\b")),
]


@dataclasses.dataclass(slots=True)
class Finding:
    """One static scan finding."""

    severity: str
    rule: str
    file: str
    line: int
    preview: str


def iter_source_files(root: pathlib.Path) -> Iterable[pathlib.Path]:
    """Yield likely code and markdown files under *root*."""
    exts = {".md", ".js", ".ts", ".tsx", ".json", ".py", ".sh", ".mjs", ".cjs"}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in exts:
            yield path


def scan(root: pathlib.Path) -> list[Finding]:
    """Scan a directory tree for suspicious patterns."""
    findings: list[Finding] = []
    for path in iter_source_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(path.relative_to(root))
        for idx, line in enumerate(text.splitlines(), start=1):
            for severity, rule, pattern in PATTERNS:
                if pattern.search(line):
                    findings.append(Finding(severity, rule, rel, idx, line[:240]))
    return findings


def _utc_timestamp() -> str:
    """Return a stable UTC timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _file_manifest(root: pathlib.Path) -> tuple[list[dict[str, Any]], str]:
    """Build a deterministic file list and bundle hash."""
    file_entries: list[dict[str, Any]] = []
    digest_lines: list[str] = []
    for path in sorted(iter_source_files(root)):
        rel = str(path.relative_to(root))
        content = path.read_bytes()
        digest = sha256_hex(content)
        file_entries.append({"path": rel, "sha256": digest, "sizeBytes": len(content)})
        digest_lines.append(f"{rel}:{digest}")
    return file_entries, sha256_hex("\n".join(digest_lines))


def _finding_counts(findings: list[Finding]) -> dict[str, int]:
    """Return counts grouped by severity."""
    counts = Counter(item.severity for item in findings)
    return dict(sorted(counts.items()))


def _stage_path(skills_root: pathlib.Path, *, stage: str, bundle_name: str) -> pathlib.Path:
    """Return the canonical destination for a staged skill bundle."""
    return skills_root / stage / bundle_name


def _stage_status(stage: str) -> str:
    """Return the manifest status label for a stage directory."""
    return "quarantined" if stage == "quarantine" else stage


def quarantine(source: pathlib.Path, destination_root: pathlib.Path) -> pathlib.Path:
    """Copy a source tree into quarantine."""
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = destination_root / source.name
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    return destination


def _build_manifest(
    *,
    source: pathlib.Path,
    findings: list[Finding],
    bundle_path: pathlib.Path,
    status: str,
) -> dict[str, Any]:
    """Build the durable scan manifest."""
    files, bundle_hash = _file_manifest(source)
    timestamp = _utc_timestamp()
    return {
        "manifestVersion": MANIFEST_VERSION,
        "scanVersion": SCAN_VERSION,
        "bundleName": source.name,
        "sourcePath": source.expanduser().resolve().as_posix(),
        "bundlePath": bundle_path.expanduser().resolve().as_posix(),
        "bundleHash": bundle_hash,
        "status": status,
        "scanTimestamp": timestamp,
        "findingCount": len(findings),
        "findingCounts": _finding_counts(findings),
        "findings": [dataclasses.asdict(item) for item in findings],
        "files": files,
        "stageHistory": [
            {
                "status": status,
                "path": bundle_path.expanduser().resolve().as_posix(),
                "timestamp": timestamp,
                "reason": "initial_scan",
            }
        ],
    }


def _load_manifest(path: pathlib.Path) -> dict[str, Any]:
    """Load and validate a scan manifest."""
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"manifest must be a JSON object: {path}")
    required = ("bundleName", "bundlePath", "status", "stageHistory")
    for key in required:
        if key not in payload:
            raise ValueError(f"manifest missing required field {key}: {path}")
    if not isinstance(payload["stageHistory"], list):
        raise ValueError(f"manifest stageHistory must be a list: {path}")
    return cast(dict[str, Any], payload)


def _write_manifest(path: pathlib.Path, manifest: dict[str, Any]) -> None:
    """Persist a scan manifest."""
    write_json(path, manifest)


def _transition_manifest(
    manifest_path: pathlib.Path,
    *,
    skills_root: pathlib.Path,
    stage: str,
    reason: str,
) -> dict[str, Any]:
    """Move a skill bundle to a reviewed stage and update its manifest."""
    if stage not in SKILL_STAGES:
        raise ValueError(f"unknown skill stage: {stage}")
    manifest = _load_manifest(manifest_path)
    current_status = str(manifest["status"])
    allowed = TRANSITIONS.get(current_status, set())
    if stage not in allowed:
        raise ValueError(f"cannot transition {current_status} -> {stage}")

    current_bundle = pathlib.Path(str(manifest["bundlePath"])).expanduser().resolve()
    destination = _stage_path(
        skills_root.expanduser().resolve(),
        stage=stage,
        bundle_name=str(manifest["bundleName"]),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)

    if current_bundle.exists():
        if current_bundle.is_relative_to(skills_root.expanduser().resolve()):
            shutil.move(current_bundle.as_posix(), destination.as_posix())
        else:
            shutil.copytree(current_bundle, destination)
    else:
        raise FileNotFoundError(current_bundle)

    manifest["bundlePath"] = destination.as_posix()
    status = _stage_status(stage)
    manifest["status"] = status
    stage_history = manifest["stageHistory"]
    assert isinstance(stage_history, list)
    cast(list[dict[str, object]], stage_history).append(
        {
            "status": status,
            "path": destination.as_posix(),
            "timestamp": _utc_timestamp(),
            "reason": reason,
        }
    )
    _write_manifest(manifest_path, manifest)
    return manifest


def _parse_legacy_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the legacy flat scanner flags."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=pathlib.Path)
    parser.add_argument("--quarantine", type=pathlib.Path)
    parser.add_argument("--report", required=True, type=pathlib.Path)
    parser.set_defaults(mode="legacy")
    return parser.parse_args(argv)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    if argv:
        first = argv[0]
        if first.startswith("-"):
            return _parse_legacy_args(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    scan_parser = sub.add_parser("scan", help="Scan a skill bundle and write a manifest.")
    scan_parser.add_argument("--source", required=True, type=pathlib.Path)
    scan_parser.add_argument("--report", required=True, type=pathlib.Path)
    scan_parser.add_argument("--quarantine-root", type=pathlib.Path)

    quarantine_parser = sub.add_parser(
        "quarantine", help="Scan a skill bundle and stage it into quarantine."
    )
    quarantine_parser.add_argument("--source", required=True, type=pathlib.Path)
    quarantine_parser.add_argument("--report", required=True, type=pathlib.Path)
    quarantine_parser.add_argument("--quarantine-root", required=True, type=pathlib.Path)

    promote_parser = sub.add_parser(
        "promote", help="Move a quarantined/reviewed skill bundle to a higher-trust stage."
    )
    promote_parser.add_argument("--manifest", required=True, type=pathlib.Path)
    promote_parser.add_argument("--skills-root", required=True, type=pathlib.Path)
    promote_parser.add_argument("--stage", choices=("reviewed", "approved"), required=True)

    demote_parser = sub.add_parser(
        "demote", help="Move a reviewed/approved skill bundle to a lower-trust stage."
    )
    demote_parser.add_argument("--manifest", required=True, type=pathlib.Path)
    demote_parser.add_argument("--skills-root", required=True, type=pathlib.Path)
    demote_parser.add_argument("--stage", choices=("quarantine", "reviewed"), required=True)

    return parser.parse_args(argv)


def _scan_and_write_manifest(
    *,
    source: pathlib.Path,
    report: pathlib.Path,
    quarantine_root: pathlib.Path | None,
) -> dict[str, Any]:
    """Run the static scan and persist its manifest."""
    findings = scan(source)
    if quarantine_root is not None:
        staged_path = quarantine(source, quarantine_root)
        status = "quarantined"
    else:
        staged_path = source.expanduser().resolve()
        status = "scanned"
    manifest = _build_manifest(
        source=source.expanduser().resolve(),
        findings=findings,
        bundle_path=staged_path,
        status=status,
    )
    _write_manifest(report, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    if getattr(args, "mode", None) == "legacy":
        manifest = _scan_and_write_manifest(
            source=args.source,
            report=args.report,
            quarantine_root=args.quarantine,
        )
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    if args.command == "scan":
        manifest = _scan_and_write_manifest(
            source=args.source,
            report=args.report,
            quarantine_root=args.quarantine_root,
        )
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0
    if args.command == "quarantine":
        manifest = _scan_and_write_manifest(
            source=args.source,
            report=args.report,
            quarantine_root=args.quarantine_root,
        )
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0
    if args.command == "promote":
        manifest = _transition_manifest(
            args.manifest,
            skills_root=args.skills_root,
            stage=args.stage,
            reason="promote",
        )
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0
    if args.command == "demote":
        manifest = _transition_manifest(
            args.manifest,
            skills_root=args.skills_root,
            stage=args.stage,
            reason="demote",
        )
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0
    raise SystemExit(f"unsupported command: {args.command}")
