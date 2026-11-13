# ACP Workers

ACP/acpx is the preferred structured worker plane for coding agents.

## Why ACP

- persistent sessions
- named workstreams
- machine-readable events
- no PTY scraping

## Included assets

- worker config templates
- coding/review system prompts
- worktree management commands
- reviewer/fixer workflow templates
- OpenClaw overlay for ACP agents

## Flow

1. create a worktree
2. run a coder ACP session
3. run tests in that worktree
4. run reviewer ACP session
5. merge only after reviewer approval

## Starting ACP workers from OpenClaw (acp profile)

OpenClaw 2026.6 routes work to a `runtime.type: "acp"` agent only through a channel binding,
`sessions_spawn({ runtime: "acp" })` or the `/acp spawn` command. It refuses ACP spawns from any
sandboxed session, because an ACP harness runs on the host outside the OpenClaw sandbox.
`sessions_spawn` also refuses requesters that are not allowed `read`, `write`, `edit`, `exec`,
`process` and `apply_patch`. That prevents an agent from escalating through a harness, and it
also means no tool-scoped agent can use the tool path.

The acp profile therefore adds exactly one unsandboxed agent, `acp-dispatch`. Workers start
from its session with `/acp spawn <harness> ...`, which OpenClaw accepts only from an
`operator.admin` client (the Control UI, or a CLI device paired with admin scope):

```text
/acp spawn codex --mode persistent --bind off --thread off --cwd /path/to/worktree
```

Follow-up turns go to the session key the command returns, for example
`openclaw agent --session-key agent:codex:acp:<id> --message "..."`.

Trade-off and the guards around it:

- The dispatcher is unsandboxed, but it has no callable tools. Its explicit allowlist names a
  tool that `deny: ["*"]` removes, so OpenClaw stops any ordinary turn addressed to it before
  the prompt reaches a model. A message cannot make it act on the host.
- It is never the default agent or a binding target, and no agent lists it in
  `subagents.allowAgents`. `acp.allowedAgents` limits `/acp spawn` to the worker harnesses
  (`codex`, `claude`). Every other agent keeps `sandbox.mode: "all"`.
  `tests/suites/contracts/platform/test_agent_sandbox_contract.py` fails if any of this drifts.
- What remains: anyone with `operator.admin` on the gateway can start a host-side ACP harness
  in a directory of their choice. That is the same trust OpenClaw already gives admin
  operators. Keep admin pairing limited to operator devices.
- OpenClaw trusts the ACP runtime only when its own installer records an npm install, so
  `clawops setup` runs `openclaw plugins install npm:@openclaw/acpx@<pin> --pin`. The pin is
  the `@openclaw/acpx` version in `platform/node-tools/package.json`. A copy loaded through
  `plugins.load.paths` stays untrusted and cannot start ACP sessions.
