"""Generic CLI entrypoint for clawops context providers."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from typing import cast

PROVIDER_COMMANDS: dict[str, tuple[str, str]] = {
    "codebase": ("clawops.context.codebase.service", "main"),
}


def _usage() -> str:
    """Return the one-line usage string."""
    providers = "|".join(sorted(PROVIDER_COMMANDS))
    return f"usage: clawops context {{{providers}}} [args...]"


def main(argv: list[str] | None = None) -> int:
    """Dispatch one context provider command."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(_usage())
        return 0 if args else 1

    provider = args.pop(0)
    target = PROVIDER_COMMANDS.get(provider)
    if target is None:
        print(f"unknown context provider: {provider}")
        return 2

    module = importlib.import_module(target[0])
    handler = getattr(module, target[1])
    if not callable(handler):
        raise TypeError(f"{target[0]}.{target[1]} is not callable")
    return cast(Callable[[list[str] | None], int], handler)(args)
