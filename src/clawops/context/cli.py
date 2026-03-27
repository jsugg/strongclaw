"""Namespace dispatcher for context providers."""

from __future__ import annotations

import sys

from clawops.context.codebase.service import main as codebase_main
from clawops.context.contracts import CONTEXT_PROVIDER_CODEBASE


def main(argv: list[str] | None = None) -> int:
    """Dispatch `clawops context` subcommands to explicit providers."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print("usage: clawops context codebase {index|query|pack|worker} [args...]")
        return 0 if args else 1

    provider = args.pop(0)
    if provider == CONTEXT_PROVIDER_CODEBASE:
        return codebase_main(args)
    print(f"unknown context provider: {provider}")
    return 2
