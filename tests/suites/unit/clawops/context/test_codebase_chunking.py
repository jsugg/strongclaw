"""Unit tests for tree-sitter-first codebase chunking."""

from __future__ import annotations

import pathlib

from clawops.context.codebase.service import build_chunks, extract_symbols


def test_build_chunks_keeps_python_decorators_with_definition() -> None:
    text = "@instrumented\ndef token_guard():\n    return True\n"

    chunks = build_chunks("auth.py", text, language="python")

    assert len(chunks) == 1
    assert chunks[0].symbol == "token_guard"
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 3
    assert chunks[0].kind == "function"
    assert chunks[0].content.startswith("@instrumented")


def test_extract_symbols_uses_tree_sitter_for_typescript_arrow_functions() -> None:
    path = pathlib.Path("auth.ts")
    text = (
        "export const tokenGuard = (token: string) => {\n"
        "  return token.length > 0;\n"
        "};\n"
        "\n"
        "export const reviewNotes = () => 'notes';\n"
    )

    symbols = extract_symbols(path, text)
    chunks = build_chunks(path.as_posix(), text, language="typescript")

    assert symbols == ["tokenGuard", "reviewNotes"]
    assert [chunk.symbol for chunk in chunks if chunk.symbol is not None] == [
        "tokenGuard",
        "reviewNotes",
    ]
    assert chunks[0].start_line == 1
    assert chunks[0].kind == "function"
    assert chunks[0].content.startswith("export const tokenGuard")
