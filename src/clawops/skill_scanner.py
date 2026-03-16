"""Static intake scanner for OpenClaw skills and bundles."""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import shutil
from typing import Iterable

from clawops.common import write_json


PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("critical", "shell exfil", re.compile(r"\b(curl|wget|Invoke-WebRequest)\b", re.IGNORECASE)),
    ("critical", "shell delete", re.compile(r"\brm\s+-rf\b", re.IGNORECASE)),
    ("high", "js child_process", re.compile(r"\bchild_process\b")),
    ("high", "js eval", re.compile(r"\beval\s*\(")),
    ("high", "python subprocess", re.compile(r"\bsubprocess\.(run|Popen|call)\b")),
    ("medium", "env read", re.compile(r"\b(process\.env|os\.environ)\b")),
    ("medium", "network client", re.compile(r"\b(requests\.(get|post)|fetch\s*\(|axios\.)")),
    ("medium", "base64 decode", re.compile(r"\bbase64\b", re.IGNORECASE)),
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
    exts = {".md", ".js", ".ts", ".tsx", ".json", ".py", ".sh"}
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
        lines = text.splitlines()
        for idx, line in enumerate(lines, start=1):
            for severity, rule, pattern in PATTERNS:
                if pattern.search(line):
                    findings.append(Finding(severity, rule, rel, idx, line[:240]))
    return findings


def quarantine(source: pathlib.Path, destination_root: pathlib.Path) -> pathlib.Path:
    """Move the source tree into quarantine."""
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = destination_root / source.name
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    return destination


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=pathlib.Path)
    parser.add_argument("--quarantine", type=pathlib.Path)
    parser.add_argument("--report", required=True, type=pathlib.Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    findings = [dataclasses.asdict(item) for item in scan(args.source)]
    result = {"findings": findings, "count": len(findings)}
    if args.quarantine is not None:
        quarantined = quarantine(args.source, args.quarantine)
        result["quarantined_to"] = str(quarantined)
    write_json(args.report, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0
