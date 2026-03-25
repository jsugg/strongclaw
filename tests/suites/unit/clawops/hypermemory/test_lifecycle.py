import pathlib
from dataclasses import replace

from clawops.hypermemory import HypermemoryEngine, load_config
from clawops.hypermemory.lifecycle import TierManager, compute_decay_score
from clawops.hypermemory.models import DecayConfig
from tests.fixtures.hypermemory import build_workspace, write_hypermemory_config


def test_compute_decay_score_fresh_item() -> None:
    config = DecayConfig(enabled=True)
    score = compute_decay_score(
        age_days=0,
        access_count=0,
        importance=0.8,
        tier="working",
        config=config,
    )
    assert 0.6 <= score <= 1.0


def test_compute_decay_score_old_no_access() -> None:
    config = DecayConfig(enabled=True)
    score = compute_decay_score(
        age_days=90,
        access_count=0,
        importance=0.1,
        tier="peripheral",
        config=config,
    )
    assert score < 0.2


def test_compute_decay_score_old_high_access() -> None:
    config = DecayConfig(enabled=True)
    score = compute_decay_score(
        age_days=90,
        access_count=20,
        importance=0.4,
        tier="working",
        config=config,
    )
    assert score > 0.3


def test_tier_manager_promotes_to_core() -> None:
    manager = TierManager(DecayConfig(enabled=True))
    assert (
        manager.evaluate_tier(
            current_tier="working",
            composite=0.9,
            access_count=12,
            importance=0.9,
            age_days=1.0,
        )
        == "core"
    )


def test_tier_manager_demotes_to_peripheral() -> None:
    manager = TierManager(DecayConfig(enabled=True))
    assert (
        manager.evaluate_tier(
            current_tier="working",
            composite=0.1,
            access_count=1,
            importance=0.2,
            age_days=90.0,
        )
        == "peripheral"
    )


def test_tier_manager_no_change() -> None:
    manager = TierManager(replace(DecayConfig(enabled=True), demote_to_peripheral_age_days=120))
    assert (
        manager.evaluate_tier(
            current_tier="working",
            composite=0.5,
            access_count=4,
            importance=0.5,
            age_days=30.0,
        )
        == "working"
    )


def test_hypermemory_lifecycle_promotes_high_value_memory(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    config = load_config(config_path)
    config = replace(config, decay=replace(config.decay, enabled=True))
    engine = HypermemoryEngine(config)
    engine.reindex()
    engine.store(
        kind="fact",
        text="The deployment checklist is the primary release gate.",
        importance=0.95,
    )

    with engine.connect() as conn:
        conn.execute("""
            UPDATE search_items
            SET access_count = 12, tier = 'working'
            WHERE snippet LIKE '%primary release gate%'
            """)
        conn.commit()

    payload = engine.run_lifecycle()
    world_text = (workspace / "bank" / "world.md").read_text(encoding="utf-8")

    assert payload["changed"] >= 1
    assert "tier=core" in world_text
