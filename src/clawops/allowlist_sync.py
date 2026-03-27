"""Render durable channel allowlist config fragments from YAML or JSON."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from typing import Any

from clawops.common import dump_json, load_json, load_yaml, write_text
from clawops.typed_values import as_mapping

TG_ID_RE = re.compile(r"^(?:tg:|telegram:)?(\d+)$")
E164_RE = re.compile(r"^\+\d{8,15}$")


def normalize_telegram(value: str) -> str:
    """Normalize a Telegram sender identifier."""
    match = TG_ID_RE.match(value.strip())
    if not match:
        raise ValueError(f"invalid Telegram id: {value}")
    return f"tg:{match.group(1)}"


def normalize_whatsapp(value: str) -> str:
    """Normalize a WhatsApp sender identifier."""
    value = value.strip()
    if not E164_RE.match(value):
        raise ValueError(f"invalid WhatsApp E.164: {value}")
    return value


def load_source(path: pathlib.Path) -> dict[str, Any]:
    """Load a structured source file."""
    if path.suffix.lower() == ".json":
        return dict(as_mapping(load_json(path), path=str(path)))
    return dict(as_mapping(load_yaml(path), path=str(path)))


def render_fragment(source: dict[str, Any]) -> dict[str, Any]:
    """Render a channel config fragment from source data."""
    telegram_allow = [normalize_telegram(item) for item in source.get("telegram_allow", [])]
    whatsapp_allow = [normalize_whatsapp(item) for item in source.get("whatsapp_allow", [])]
    telegram_models = source.get("telegram_models", {})
    whatsapp_models = source.get("whatsapp_models", {})
    return {
        "channels": {
            "telegram": {"allowFrom": telegram_allow},
            "whatsapp": {"allowFrom": whatsapp_allow},
            "modelByChannel": {
                "telegram": telegram_models,
                "whatsapp": whatsapp_models,
            },
        }
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    fragment = render_fragment(load_source(args.source))
    write_text(args.output, dump_json(fragment))
    print(json.dumps(fragment, indent=2, sort_keys=True))
    return 0
