# Policy Engine and Wrappers

## Problem

Built-in tool policy is necessary but not sufficient for platform-grade side effects. External actions need:
- explicit allowlists
- approvals
- journaling
- idempotency
- replay visibility

## Included solution

- YAML policy bundle
- SQLite operation journal
- GitHub comment/label/merge wrappers and a generic webhook wrapper
- optional Rego examples for future policy engines

## Wrapper pattern

1. evaluate policy
2. create or reuse op-journal entry
3. stamp a wrapper-owned execution contract onto executable rows
4. if approval is required, transition to `pending_approval` and stop
5. record explicit approval in the journal
6. execute the side effect from the `approved` state only
7. record terminal state

## Operation states

- `proposed`
- `pending_approval`
- `approved`
- `running`
- `succeeded`
- `failed`
- `cancelled`

## Replay semantics

Wrapper operations are idempotent by `(scope, idempotency_key)`.

Replaying the same request:

- returns the existing `pending_approval` result if approval is still required
- executes from `approved` if the operation is ready to run
- returns the cached terminal result for `succeeded` and `failed` operations
- never replays a terminal side effect automatically

Failed replay semantics stay explicit:

- policy-denied failures replay as `ok: false`, `accepted: false`, `executed: false`
- execution-time failures replay as `ok: false`, `accepted: true`, `executed: true`
- cached terminal failures include the persisted status/body summary when available

## Execution contract

Wrappers now persist an execution contract alongside the stored policy decision.
That contract binds execution to the prepared operation metadata:

- scope
- kind
- trust zone
- normalized target
- input hash
- policy decision

`clawops op-journal begin` remains available for generic audit/bookkeeping use,
but those rows are not executable wrapper operations by themselves.

This prevents a forged journal row from bypassing policy and allowlist checks at
`--execute-approved` time.

## Journal ownership

Treat `~/.openclaw/clawops` as service-owned state.

- keep directory mode `0700` on `~/.openclaw/clawops`
- keep file mode `0600` on `~/.openclaw/clawops/op_journal.sqlite`
- do not grant write access to lower-trust workers or shared workspaces

## CLI flow

Prepare an approval-gated webhook:

```bash
clawops wrapper webhook \
  --policy platform/configs/policy/policy.yaml \
  --db ~/.openclaw/clawops/op_journal.sqlite \
  --scope telegram:owner \
  --trust-zone automation \
  --url https://example.internal/hooks/deploy \
  --payload-file payload.json
```

Approve the operation:

```bash
clawops op-journal approve \
  --db ~/.openclaw/clawops/op_journal.sqlite \
  --op-id <op-id> \
  --approved-by operator \
  --note "approved after review"
```

Execute the approved operation:

```bash
clawops wrapper webhook \
  --db ~/.openclaw/clawops/op_journal.sqlite \
  --op-id <op-id> \
  --execute-approved
```

Legacy note:

- older approved rows that predate execution contracts may require an explicit
  policy file on `--execute-approved` so the wrapper can restamp the row before
  sending the side effect
