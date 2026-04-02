# Security Model

## Core rules

1. Access control before intelligence.
2. One trusted operator boundary per gateway.
3. No public control-plane exposure.
4. Plugins, skills, and MCP integrations are supply-chain code.
5. Browser automation is isolated and off by default.

## Trust zones

- `reader`: hostile content, read-only
- `coder`: sandboxed mutation
- `reviewer`: read-only verification
- `messaging`: channel-only lane
- `admin`: trusted operator lane

## Review requirements

Require an independent reviewer for:
- auth
- secrets
- infrastructure
- CI/CD
- dependency changes
- browser automation enablement

Repository enforcement:
- `.github/workflows/security.yml` runs `tests/scripts/security_workflow.py enforce-independent-review`.
- The check inspects pull-request file changes in security-critical paths and fails unless there is at least one non-author `APPROVED` review.

## Skill intake

Use: `clawops skill-scan --source <path> --quarantine <dir> --report <file>`

Never auto-enable a new skill or plugin directly from a download path.
