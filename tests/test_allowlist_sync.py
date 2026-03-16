"""Unit tests for allowlist rendering."""

from __future__ import annotations

from clawops.allowlist_sync import render_fragment


def test_render_fragment_normalizes_ids() -> None:
    fragment = render_fragment(
        {
            "telegram_allow": ["12345678"],
            "whatsapp_allow": ["+5511999999999"],
            "telegram_models": {"12345678": "readerfast"},
            "whatsapp_models": {},
        }
    )
    assert fragment["channels"]["telegram"]["allowFrom"] == ["tg:12345678"]
