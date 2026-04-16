"""Unit tests for hypermemory/canonical_store_helpers.py."""

from __future__ import annotations

import pathlib

from clawops.hypermemory import load_config
from clawops.hypermemory.canonical_store_helpers import (
    infer_fact_key,
    is_noise_entry,
    normalize_tier,
    passes_admission,
)
from clawops.hypermemory.capture import CaptureCandidate
from tests.utils.helpers.hypermemory import build_workspace, write_hypermemory_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: pathlib.Path) -> object:
    """Return a loaded HypermemoryConfig backed by a temp workspace."""
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)
    return load_config(config_path)


# ---------------------------------------------------------------------------
# is_noise_entry
# ---------------------------------------------------------------------------


def test_is_noise_entry_short_text(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    assert is_noise_entry("Hi", config=config)  # type: ignore[arg-type]


def test_is_noise_entry_greeting(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    assert is_noise_entry("Hello", config=config)  # type: ignore[arg-type]


def test_is_noise_entry_denial_phrase(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    assert is_noise_entry("I don't remember any matching memory.", config=config)  # type: ignore[arg-type]


def test_is_noise_entry_valid_fact_not_noise(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    text = "Deploy approvals require two reviewers with write access."
    assert not is_noise_entry(text, config=config)  # type: ignore[arg-type]


def test_is_noise_entry_json_blob(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    assert is_noise_entry('{"results": []}', config=config)  # type: ignore[arg-type]


def test_is_noise_entry_fresh_session(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    assert is_noise_entry("fresh session", config=config)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# passes_admission
# ---------------------------------------------------------------------------


def _candidate(kind: str, confidence: float | None = None) -> CaptureCandidate:
    return CaptureCandidate(
        kind=kind,  # type: ignore[arg-type]
        text="some text for admission testing",
        confidence=confidence,
    )


def test_passes_admission_disabled_always_passes(tmp_path: pathlib.Path) -> None:
    config = _make_config(tmp_path)
    # When admission is disabled, everything passes
    candidate = _candidate("fact", confidence=0.0)
    # Patch admission.enabled to False in config to test disabled path
    from dataclasses import replace

    from clawops.hypermemory.models import HypermemoryConfig

    cfg: HypermemoryConfig = config  # type: ignore[assignment]
    disabled_cfg = replace(cfg, admission=replace(cfg.admission, enabled=False))
    assert passes_admission(candidate, config=disabled_cfg)


def test_passes_admission_high_confidence_passes(tmp_path: pathlib.Path) -> None:
    from dataclasses import replace

    from clawops.hypermemory.models import HypermemoryConfig

    config: HypermemoryConfig = _make_config(tmp_path)  # type: ignore[assignment]
    enabled_cfg = replace(
        config,
        admission=replace(config.admission, enabled=True, min_confidence=0.5),
    )
    candidate = _candidate("fact", confidence=0.9)
    assert passes_admission(candidate, config=enabled_cfg)


def test_passes_admission_low_confidence_fails(tmp_path: pathlib.Path) -> None:
    from dataclasses import replace

    from clawops.hypermemory.models import HypermemoryConfig

    config: HypermemoryConfig = _make_config(tmp_path)  # type: ignore[assignment]
    enabled_cfg = replace(
        config,
        admission=replace(config.admission, enabled=True, min_confidence=0.8),
    )
    candidate = _candidate("fact", confidence=0.3)
    assert not passes_admission(candidate, config=enabled_cfg)


def test_passes_admission_none_confidence_passes_if_prior_ok(tmp_path: pathlib.Path) -> None:
    from dataclasses import replace

    from clawops.hypermemory.models import HypermemoryConfig

    config: HypermemoryConfig = _make_config(tmp_path)  # type: ignore[assignment]
    enabled_cfg = replace(
        config,
        admission=replace(
            config.admission,
            enabled=True,
            min_confidence=0.5,
            type_priors={"fact": 0.9},
        ),
    )
    candidate = _candidate("fact", confidence=None)
    assert passes_admission(candidate, config=enabled_cfg)


def test_passes_admission_low_prior_fails(tmp_path: pathlib.Path) -> None:
    from dataclasses import replace

    from clawops.hypermemory.models import HypermemoryConfig

    config: HypermemoryConfig = _make_config(tmp_path)  # type: ignore[assignment]
    enabled_cfg = replace(
        config,
        admission=replace(
            config.admission,
            enabled=True,
            min_confidence=0.7,
            type_priors={"fact": 0.3},
        ),
    )
    candidate = _candidate("fact", confidence=0.9)
    assert not passes_admission(candidate, config=enabled_cfg)


# ---------------------------------------------------------------------------
# normalize_tier
# ---------------------------------------------------------------------------


def test_normalize_tier_core() -> None:
    assert normalize_tier("core") == "core"


def test_normalize_tier_working() -> None:
    assert normalize_tier("working") == "working"


def test_normalize_tier_peripheral() -> None:
    assert normalize_tier("peripheral") == "peripheral"


def test_normalize_tier_invalid_returns_working() -> None:
    assert normalize_tier("unknown_value") == "working"


def test_normalize_tier_none_returns_working() -> None:
    assert normalize_tier(None) == "working"


def test_normalize_tier_case_insensitive() -> None:
    assert normalize_tier("Core") == "core"
    assert normalize_tier("WORKING") == "working"


def test_normalize_tier_with_whitespace() -> None:
    assert normalize_tier("  peripheral  ") == "peripheral"


def test_normalize_tier_empty_string() -> None:
    assert normalize_tier("") == "working"


# ---------------------------------------------------------------------------
# infer_fact_key
# ---------------------------------------------------------------------------


def test_infer_fact_key_user_name() -> None:
    assert infer_fact_key(kind="fact", text="My name is Alice") == "user:name"


def test_infer_fact_key_user_timezone() -> None:
    assert infer_fact_key(kind="fact", text="My timezone is UTC+1") == "user:timezone"


def test_infer_fact_key_user_role() -> None:
    assert infer_fact_key(kind="entity", text="My role is senior engineer") == "user:role"


def test_infer_fact_key_user_team() -> None:
    assert infer_fact_key(kind="entity", text="My team is platform") == "user:team"


def test_infer_fact_key_editor_pref() -> None:
    result = infer_fact_key(kind="opinion", text="I use neovim as my editor")
    assert result == "pref:editor"


def test_infer_fact_key_no_match_returns_none() -> None:
    assert infer_fact_key(kind="fact", text="The sky is blue.") is None


def test_infer_fact_key_empty_text_returns_none() -> None:
    assert infer_fact_key(kind="fact", text="") is None


def test_infer_fact_key_kind_param_ignored() -> None:
    # kind is explicitly deleted in the implementation; all kinds use the same rules
    result_fact = infer_fact_key(kind="fact", text="My name is Bob")
    result_opinion = infer_fact_key(kind="opinion", text="My name is Bob")
    assert result_fact == result_opinion == "user:name"


def test_infer_fact_key_case_insensitive() -> None:
    assert infer_fact_key(kind="fact", text="MY NAME IS Alice") == "user:name"
