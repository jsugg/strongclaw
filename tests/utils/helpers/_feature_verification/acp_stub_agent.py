"""Minimal ACP (Agent Client Protocol) agent that stands in for vendor coding agents.

Real ``acpx`` launches this over stdio in place of a vendor adapter (codex-acp,
claude-agent-acp, ``gemini --acp`` ...), so StrongClaw's auth-mode routing, env sanitization,
and acpx invocation are exercised end to end without vendor accounts. The reply reports which
auth-relevant env keys reached the agent process (presence only, never values) and which auth
method acpx selected, so tests can assert routing without the stub ever handling a secret.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections.abc import Mapping
from typing import Final, TextIO, cast

PROTOCOL_VERSION: Final[int] = 1
REPLY_PREFIX: Final[str] = "FEATVER_ACP_STUB"
# Env keys whose presence (never value) the stub reports back.
OBSERVED_ENV_KEYS: Final[tuple[str, ...]] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_PROFILE",
    "AWS_REGION",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "VERTEXAI_PROJECT",
    "ACPX_AUTH_OPENAI_API_KEY",
)
# Auth methods advertised by the real adapters each stub persona imitates.
AUTH_METHODS: Final[Mapping[str, tuple[str, ...]]] = {
    "codex": ("chatgpt", "codex-api-key", "openai-api-key"),
    "claude": ("claude-ai-login", "console-login"),
}

type JsonObject = dict[str, object]


class StubAgent:
    """Serve one ACP connection on stdio."""

    def __init__(self, persona: str, *, out: TextIO) -> None:
        self._persona = persona
        self._out = out
        self._authenticated: list[str] = []

    def _send(self, payload: JsonObject) -> None:
        self._out.write(json.dumps(payload) + "\n")
        self._out.flush()

    def _result(self, request_id: object, result: JsonObject) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _error(self, request_id: object, code: int, message: str) -> None:
        self._send(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
        )

    def _env_report(self) -> str:
        return ",".join(
            f"{key}:{'present' if os.environ.get(key) else 'absent'}" for key in OBSERVED_ENV_KEYS
        )

    def _prompt_text(self, params: JsonObject) -> str:
        prompt = params.get("prompt")
        if not isinstance(prompt, list):
            return ""
        parts: list[str] = []
        for block in cast(list[object], prompt):
            if isinstance(block, dict):
                text = cast(Mapping[str, object], block).get("text")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(parts)

    def handle(self, message: JsonObject) -> None:
        """Dispatch one JSON-RPC message."""
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params")
        params_mapping = cast(JsonObject, params) if isinstance(params, dict) else {}
        if method == "initialize":
            self._result(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "agentCapabilities": {
                        "loadSession": False,
                        "promptCapabilities": {
                            "image": False,
                            "audio": False,
                            "embeddedContext": False,
                        },
                    },
                    "authMethods": [
                        {"id": method_id, "name": method_id, "description": None}
                        for method_id in AUTH_METHODS.get(self._persona, ())
                    ],
                    "agentInfo": {"name": f"featver-{self._persona}", "version": "1.0.0"},
                },
            )
        elif method == "authenticate":
            method_id = params_mapping.get("methodId")
            self._authenticated.append(str(method_id))
            self._result(request_id, {})
        elif method == "session/new":
            self._result(request_id, {"sessionId": f"featver-{uuid.uuid4().hex[:12]}"})
        elif method == "session/prompt":
            session_id = params_mapping.get("sessionId")
            reply = (
                f"{REPLY_PREFIX} persona={self._persona} "
                f"auth={'+'.join(self._authenticated) or 'none'} "
                f"env={self._env_report()} prompt={self._prompt_text(params_mapping)}"
            )
            self._send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": reply},
                        },
                    },
                }
            )
            self._result(request_id, {"stopReason": "end_turn"})
        elif method in {"session/cancel"} or request_id is None:
            return
        elif str(method).startswith("session/"):
            # Optional session controls (modes, config options): accept as no-ops.
            self._result(request_id, {})
        else:
            self._error(request_id, -32601, f"method not found: {method}")

    def serve(self, stream: TextIO) -> None:
        """Read newline-delimited JSON-RPC messages until stdin closes."""
        for raw_line in stream:
            line = raw_line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict):
                self.handle(cast(JsonObject, message))


def main(argv: list[str] | None = None) -> int:
    """Run the stub agent on stdio."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persona", required=True)
    args = parser.parse_args(argv)
    StubAgent(str(args.persona), out=sys.stdout).serve(sys.stdin)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
