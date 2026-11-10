"""Attribution guard: reject AI-tool attribution in refs, commits, and pull-request metadata.

The guard bans attribution only. A provider or dependency named as the subject of the work
passes; the vendored patterns in `attribution_patterns` encode that distinction.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from tests.utils.helpers._ci_workflows.attribution_patterns import (
    EMAIL_RE,
    LABEL_RE,
    MSG_RE,
    REF_RE,
)
from tests.utils.helpers._ci_workflows.common import CiWorkflowError, run_checked
from tests.utils.helpers._ci_workflows.github_api import (
    GITHUB_API_BASE,
    github_get_object,
    github_paginated_get,
)

type AttributionRule = Literal["ref-name", "commit-message", "email", "label"]

ZERO_SHA: Final[str] = "0" * 40
# git strips everything below this line (the `commit -v` diff) before recording the message.
_SCISSORS_LINE: Final[str] = "# ------------------------ >8 ------------------------"
_FIELD_SEPARATOR: Final[str] = "\x1f"
_RECORD_SEPARATOR: Final[str] = "\x1e"
_RULE_PATTERNS: Final[Mapping[AttributionRule, re.Pattern[str]]] = {
    "ref-name": REF_RE,
    "commit-message": MSG_RE,
    "email": EMAIL_RE,
    "label": LABEL_RE,
}


@dataclass(frozen=True)
class AttributionFinding:
    """One field whose value matched an attribution pattern."""

    field: str
    rule: AttributionRule
    match: str


@dataclass(frozen=True)
class CommitRecord:
    """The attribution-relevant parts of one commit."""

    sha: str
    message: str
    author_email: str
    committer_email: str


@dataclass(frozen=True)
class PullRequestSnapshot:
    """Pull-request metadata refetched from the API at check time."""

    number: int
    title: str
    body: str
    head_ref: str
    labels: tuple[str, ...]
    commits: tuple[CommitRecord, ...]


@dataclass(frozen=True)
class PushTarget:
    """One pushed ref plus the git revision arguments selecting its new commits."""

    remote_ref: str
    rev_args: tuple[str, ...]


def scan_value(rule: AttributionRule, *, field: str, value: str) -> AttributionFinding | None:
    """Return a finding when *value* matches the pattern for *rule*."""
    match = _RULE_PATTERNS[rule].search(value)
    if match is None:
        return None
    return AttributionFinding(field=field, rule=rule, match=match.group(0).strip() or value)


def _collect(
    candidates: Iterable[tuple[AttributionRule, str, str]],
) -> list[AttributionFinding]:
    """Scan (rule, field, value) triples and keep the matches."""
    findings: list[AttributionFinding] = []
    for rule, field, value in candidates:
        finding = scan_value(rule, field=field, value=value)
        if finding is not None:
            findings.append(finding)
    return findings


def _commit_candidates(commit: CommitRecord) -> list[tuple[AttributionRule, str, str]]:
    """Return the scan triples for one commit."""
    prefix = f"commit {commit.sha[:12]}"
    return [
        ("commit-message", f"{prefix} message", commit.message),
        ("email", f"{prefix} author email", commit.author_email),
        ("email", f"{prefix} committer email", commit.committer_email),
    ]


def scan_commits(commits: Iterable[CommitRecord]) -> list[AttributionFinding]:
    """Scan every commit message plus its author and committer emails."""
    return _collect(candidate for commit in commits for candidate in _commit_candidates(commit))


def scan_pull_request(snapshot: PullRequestSnapshot) -> list[AttributionFinding]:
    """Scan pull-request title, body, head ref, labels, and every commit."""
    candidates: list[tuple[AttributionRule, str, str]] = [
        ("commit-message", "pull request title", snapshot.title),
        ("commit-message", "pull request body", snapshot.body),
        ("ref-name", "pull request head ref", snapshot.head_ref),
    ]
    candidates.extend(
        ("label", f"pull request label {label!r}", label) for label in snapshot.labels
    )
    for commit in snapshot.commits:
        candidates.extend(_commit_candidates(commit))
    return _collect(candidates)


def format_findings(findings: Sequence[AttributionFinding], *, scope: str) -> str:
    """Render a pass/fail report naming every matched field."""
    if not findings:
        return f"attribution guard: PASS ({scope})"
    lines = [f"attribution guard: FAIL ({scope}); remove the attribution and retry"]
    lines.extend(
        f"- {finding.field}: matched {finding.rule} pattern {finding.match!r}"
        for finding in findings
    )
    return "\n".join(lines)


def load_commit_message(path: Path) -> str:
    """Read a commit-msg file the way git's default cleanup records it."""
    kept: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line == _SCISSORS_LINE:
            break
        if line.startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _required_string(payload: Mapping[str, object], key: str, *, label: str) -> str:
    """Return one required string field from an API payload."""
    value = payload.get(key)
    if not isinstance(value, str):
        raise CiWorkflowError(f"{label}.{key} must be a string")
    return value


def _required_object(payload: Mapping[str, object], key: str, *, label: str) -> dict[str, object]:
    """Return one required object field from an API payload."""
    value = payload.get(key)
    if not isinstance(value, dict):
        raise CiWorkflowError(f"{label}.{key} must be an object")
    return {
        item_key: item
        for item_key, item in cast(dict[object, object], value).items()
        if isinstance(item_key, str)
    }


def _api_commit_record(row: Mapping[str, object]) -> CommitRecord:
    """Validate one `pulls/{n}/commits` row."""
    sha = _required_string(row, "sha", label="commit")
    commit = _required_object(row, "commit", label=f"commit {sha}")
    author = _required_object(commit, "author", label=f"commit {sha}.commit")
    committer = _required_object(commit, "committer", label=f"commit {sha}.commit")
    return CommitRecord(
        sha=sha,
        message=_required_string(commit, "message", label=f"commit {sha}.commit"),
        author_email=_required_string(author, "email", label=f"commit {sha}.commit.author"),
        committer_email=_required_string(
            committer, "email", label=f"commit {sha}.commit.committer"
        ),
    )


def _label_names(value: object) -> tuple[str, ...]:
    """Validate the pull-request labels array."""
    if not isinstance(value, list):
        raise CiWorkflowError("pull_request.labels must be a list")
    names: list[str] = []
    for entry in cast(list[object], value):
        if not isinstance(entry, dict):
            raise CiWorkflowError("pull_request.labels entries must be objects")
        name = cast(dict[object, object], entry).get("name")
        if not isinstance(name, str):
            raise CiWorkflowError("pull_request.labels[].name must be a string")
        names.append(name)
    return tuple(names)


def fetch_pull_request_snapshot(
    *,
    repository: str,
    number: int,
    token: str,
    api_base: str = GITHUB_API_BASE,
) -> PullRequestSnapshot:
    """Refetch live pull-request metadata instead of trusting the event payload."""
    if not token.strip():
        raise CiWorkflowError("a GitHub token is required to refetch pull-request metadata")
    pull_url = f"{api_base.rstrip('/')}/repos/{repository}/pulls/{number}"
    pull = github_get_object(url=pull_url, token=token)
    body = pull.get("body")
    if body is not None and not isinstance(body, str):
        raise CiWorkflowError("pull_request.body must be a string or null")
    expected_commits = pull.get("commits")
    if not isinstance(expected_commits, int):
        raise CiWorkflowError("pull_request.commits must be an integer")
    head = _required_object(pull, "head", label="pull_request")
    commits = tuple(
        _api_commit_record(row)
        for row in github_paginated_get(url=f"{pull_url}/commits?per_page=100", token=token)
    )
    if len(commits) != expected_commits:
        # The commits endpoint caps out at 250 entries; never pass a partially verified PR.
        raise CiWorkflowError(
            f"fetched {len(commits)} of {expected_commits} pull-request commits; "
            "refusing to report a partially verified pull request"
        )
    return PullRequestSnapshot(
        number=number,
        title=_required_string(pull, "title", label="pull_request"),
        body=body or "",
        head_ref=_required_string(head, "ref", label="pull_request.head"),
        labels=_label_names(pull.get("labels")),
        commits=commits,
    )


def read_git_commits(rev_args: Sequence[str], *, cwd: Path) -> tuple[CommitRecord, ...]:
    """Read message plus author/committer email for every commit selected by *rev_args*."""
    output_format = (
        f"--format=%H{_FIELD_SEPARATOR}%ae{_FIELD_SEPARATOR}%ce{_FIELD_SEPARATOR}%B"
        f"{_RECORD_SEPARATOR}"
    )
    result = run_checked(["git", "log", output_format, *rev_args], cwd=cwd, capture_output=True)
    records: list[CommitRecord] = []
    for chunk in result.stdout.split(_RECORD_SEPARATOR):
        record = chunk.lstrip("\n")
        if not record:
            continue
        fields = record.split(_FIELD_SEPARATOR, 3)
        if len(fields) != 4:
            raise CiWorkflowError(f"unexpected git log record: {record[:80]!r}")
        sha, author_email, committer_email, message = fields
        records.append(
            CommitRecord(
                sha=sha,
                message=message,
                author_email=author_email,
                committer_email=committer_email,
            )
        )
    return tuple(records)


def pre_push_target_from_env(environ: Mapping[str, str]) -> PushTarget:
    """Describe the pushed ref from the variables pre-commit exports to pre-push hooks."""
    remote_ref = environ.get("PRE_COMMIT_REMOTE_BRANCH", "").strip()
    if not remote_ref:
        raise CiWorkflowError(
            "PRE_COMMIT_REMOTE_BRANCH is unset; run this check through the pre-commit "
            "pre-push hook"
        )
    from_ref = environ.get("PRE_COMMIT_FROM_REF", "").strip()
    to_ref = environ.get("PRE_COMMIT_TO_REF", "").strip()
    if from_ref and to_ref:
        return PushTarget(remote_ref=remote_ref, rev_args=(f"{from_ref}..{to_ref}",))
    # pre-commit omits the range when the push introduces a root commit.
    local_ref = environ.get("PRE_COMMIT_LOCAL_BRANCH", "").strip()
    remote_name = environ.get("PRE_COMMIT_REMOTE_NAME", "").strip()
    if not local_ref or not remote_name:
        raise CiWorkflowError("pre-push environment lacks both a commit range and a local ref")
    return PushTarget(
        remote_ref=remote_ref,
        rev_args=(local_ref, "--not", f"--remotes={remote_name}"),
    )


def push_rev_args(*, before: str, after: str) -> tuple[str, ...]:
    """Return the git revision arguments covering one pushed `before..after` update."""
    if not after.strip() or after == ZERO_SHA:
        raise CiWorkflowError("push audit requires the pushed commit sha")
    if before.strip() and before != ZERO_SHA:
        return (f"{before}..{after}",)
    return ("--max-count=1", after)


def scan_push(target: PushTarget, *, cwd: Path) -> list[AttributionFinding]:
    """Scan one pushed ref name plus every commit it introduces."""
    findings = _collect([("ref-name", "pushed ref", target.remote_ref)])
    findings.extend(scan_commits(read_git_commits(target.rev_args, cwd=cwd)))
    return findings
