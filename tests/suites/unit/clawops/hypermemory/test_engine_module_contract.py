from __future__ import annotations

import importlib

import pytest

from tests.utils.helpers.repo import REPO_ROOT


@pytest.mark.parametrize(
    ("module_name", "relative_path"),
    [
        ("clawops.hypermemory._engine.backend", "src/clawops/hypermemory/_engine/backend.py"),
        ("clawops.hypermemory._engine.indexing", "src/clawops/hypermemory/_engine/indexing.py"),
        ("clawops.hypermemory._engine.query", "src/clawops/hypermemory/_engine/query.py"),
        ("clawops.hypermemory._engine.storage", "src/clawops/hypermemory/_engine/storage.py"),
        ("clawops.hypermemory._engine.verify", "src/clawops/hypermemory/_engine/verify.py"),
    ],
)
def test_hypermemory_private_engine_modules_exist(
    module_name: str,
    relative_path: str,
) -> None:
    module = importlib.import_module(module_name)

    assert module is not None
    assert (REPO_ROOT / relative_path).is_file()


@pytest.mark.parametrize(
    "relative_path",
    [
        "src/clawops/hypermemory/engine_backend.py",
        "src/clawops/hypermemory/engine_indexing.py",
        "src/clawops/hypermemory/engine_memory.py",
        "src/clawops/hypermemory/engine_query.py",
    ],
)
def test_legacy_flat_engine_modules_are_removed(relative_path: str) -> None:
    assert not (REPO_ROOT / relative_path).exists()
