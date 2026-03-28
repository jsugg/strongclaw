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

## Skill intake

Use:
`clawops skill-scan --source <path> --quarantine <dir> --report <file>`

Never auto-enable a new skill or plugin directly from a download path.
