from clawops.hypermemory.parser import parse_typed_entry


def test_parse_entry_with_lifecycle_metadata() -> None:
    entry = parse_typed_entry(
        "Fact[scope=project:strongclaw,importance=0.80,tier=core,accessed=5,last_access=2026-03-24,fact_key=user:timezone]: My timezone is UTC-3",
        default_scope="project:strongclaw",
    )

    assert entry is not None
    assert entry.importance == 0.8
    assert entry.tier == "core"
    assert entry.access_count == 5
    assert entry.last_access_date == "2026-03-24"
    assert entry.fact_key == "user:timezone"


def test_parse_entry_with_feedback_metadata() -> None:
    entry = parse_typed_entry(
        "Fact[scope=project:strongclaw,injected=5,confirmed=3,bad_recall=1]: Deploy approvals require two reviewers.",
        default_scope="project:strongclaw",
    )

    assert entry is not None
    assert entry.injected_count == 5
    assert entry.confirmed_count == 3
    assert entry.bad_recall_count == 1


def test_parse_entry_without_new_metadata_uses_defaults() -> None:
    entry = parse_typed_entry(
        "Fact[scope=project:strongclaw]: Deploy approvals require two reviewers.",
        default_scope="project:strongclaw",
    )

    assert entry is not None
    assert entry.importance is None
    assert entry.tier == "working"
    assert entry.access_count == 0
