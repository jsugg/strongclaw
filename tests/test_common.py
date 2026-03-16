"""Unit tests for common helpers."""

from __future__ import annotations

from clawops.common import deep_merge


def test_deep_merge_nested_mapping() -> None:
    base = {"gateway": {"bind": "loopback", "auth": {"mode": "token"}}}
    overlay = {"gateway": {"auth": {"allowTailscale": False}}}
    assert deep_merge(base, overlay) == {
        "gateway": {
            "bind": "loopback",
            "auth": {"mode": "token", "allowTailscale": False},
        }
    }
