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
- GitHub, Jira, and generic webhook wrappers
- optional Rego examples for future policy engines

## Wrapper pattern

1. evaluate policy
2. create or reuse op-journal entry
3. require approval when needed
4. execute side effect
5. record terminal state
