"""Loopback HTTP helpers for probing sidecars from feature tests."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """One HTTP response."""

    status: int
    body: str

    def json(self) -> object:
        """Decode the body as JSON."""
        return cast(object, json.loads(self.body)) if self.body else {}


# Loopback probes must never route through system or environment proxies.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def basic_auth(user: str, password: str) -> dict[str, str]:
    """Return a basic-auth header."""
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def http_request(
    method: str,
    url: str,
    *,
    body: object = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> HttpResponse:
    """Issue one loopback HTTP request and return status plus body, even for HTTP errors."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return HttpResponse(int(response.status), response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return HttpResponse(int(exc.code), exc.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, OSError) as exc:
        # Status 0 marks "no HTTP response" so callers can poll and assert uniformly.
        return HttpResponse(0, f"unreachable: {exc}")
