"""Vendored attribution patterns shared with the GitHub rulesets and local agent hooks.

Keep every pattern string byte-identical to the shared reference; the unit suite pins
their SHA-256 fingerprints so drift between repositories fails loudly. The strings are
RE2-compatible (GitHub rulesets) and valid Python `re` syntax.
"""

from __future__ import annotations

import re
from typing import Final

AGENT_NAMES: Final[str] = r"(?:claude|anthropic|codex|openai|chatgpt|copilot)"
TOOL_NAMES: Final[str] = (
    r"(?:claude|anthropic|codex|openai|chatgpt|copilot|gemini|cursor|windsurf|devin|aider|cline)"
)

# Tool name used as a namespace (claude/..., ai-codex/...), not as the subject of the work.
REF_NAME: Final[str] = (
    rf"(?i)^(?:refs/(?:heads|tags)/)?(?:(?:ai|agents?|assistants?|bots?|llm)[/._-])?{AGENT_NAMES}(?:/|\n?$)"
)

COMMIT_MESSAGE: Final[str] = (
    r"(?im)"
    rf"(?:^[ \t]*(?:co-authored-by|assisted-by|generated-by|ai-assisted-by)[ \t]*:[^\n]*{TOOL_NAMES})"
    rf"|(?:^[^a-z0-9\n]{{0,8}}(?:generated|created|written|authored|produced|assisted)[ \t]+(?:by|with|using|via)[ \t]+(?:an?[ \t]+|the[ \t]+)?\[?{TOOL_NAMES}(?:[^a-z0-9\n-]|\n?$))"
    r"|(?:claude\.ai/(?:code|share)|claude\.com/claude-code|chatgpt\.com/(?:codex|share))"
    r"|(?:noreply@(?:anthropic|openai)\.com)"
    rf"|(?:^merge pull request #[0-9]+ from [^ \n]+/{AGENT_NAMES}/)"
)

EMAIL: Final[str] = r"(?i)@(?:anthropic|openai)\.com\n?$"

# Labels: the owner's rule bans these words anywhere in a label.
LABEL: Final[str] = r"(?i)(?:claude|anthropic|codex|openai)"

REF_RE: Final[re.Pattern[str]] = re.compile(REF_NAME)
MSG_RE: Final[re.Pattern[str]] = re.compile(COMMIT_MESSAGE)
EMAIL_RE: Final[re.Pattern[str]] = re.compile(EMAIL)
LABEL_RE: Final[re.Pattern[str]] = re.compile(LABEL)
