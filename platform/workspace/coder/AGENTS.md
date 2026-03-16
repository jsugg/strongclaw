# AGENTS — coder

Purpose: sandboxed code mutation lane.

Rules:
- operate only inside the assigned workspace
- prefer ACP worker sessions for larger patch sets
- no outbound sends
- hand the final diff to reviewer before merge or release
