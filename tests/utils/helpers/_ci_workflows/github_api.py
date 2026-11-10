"""Minimal typed GitHub REST client shared by CI workflow helpers."""

from __future__ import annotations

import json
import urllib.request
from typing import Final, cast

from tests.utils.helpers._ci_workflows.common import CiWorkflowError

GITHUB_API_BASE: Final[str] = "https://api.github.com"
_REQUEST_TIMEOUT_SECONDS: Final[int] = 30


def _headers(token: str) -> dict[str, str]:
    """Return the standard GitHub REST headers for one bearer token."""
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _fetch(url: str, *, token: str) -> tuple[object, str | None]:
    """Fetch one URL and return its decoded JSON payload plus the Link header."""
    request = urllib.request.Request(url, headers=_headers(token), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
            link_header = response.headers.get("Link")
    except OSError as exc:
        raise CiWorkflowError(f"github api request failed for {url}: {exc}") from exc
    try:
        payload: object = json.loads(body)
    except json.JSONDecodeError as exc:
        raise CiWorkflowError(f"github api returned invalid json for {url}") from exc
    return payload, link_header


def _string_keyed(row: object, *, url: str) -> dict[str, object]:
    """Validate one JSON object and keep only its string keys."""
    if not isinstance(row, dict):
        raise CiWorkflowError(f"github api row must be an object for {url}")
    return {
        key: value for key, value in cast(dict[object, object], row).items() if isinstance(key, str)
    }


def next_link(link_header: str | None) -> str | None:
    """Return the rel="next" URL from a GitHub Link header, if present."""
    if link_header is None:
        return None
    for segment in link_header.split(","):
        parts = [part.strip() for part in segment.split(";")]
        if not parts:
            continue
        if any(part == 'rel="next"' for part in parts[1:]):
            candidate = parts[0]
            if candidate.startswith("<") and candidate.endswith(">"):
                return candidate[1:-1]
    return None


def github_get_object(*, url: str, token: str) -> dict[str, object]:
    """Fetch one GitHub API endpoint that returns a JSON object."""
    payload, _ = _fetch(url, token=token)
    return _string_keyed(payload, url=url)


def github_paginated_get(*, url: str, token: str) -> list[dict[str, object]]:
    """Fetch one GitHub API list endpoint and follow pagination links."""
    collected: list[dict[str, object]] = []
    next_url: str | None = url
    while next_url is not None:
        payload, link_header = _fetch(next_url, token=token)
        if not isinstance(payload, list):
            raise CiWorkflowError(f"github api payload must be a list for {next_url}")
        current_url = next_url
        collected.extend(_string_keyed(row, url=current_url) for row in cast(list[object], payload))
        next_url = next_link(link_header)
    return collected
