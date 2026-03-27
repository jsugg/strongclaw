from __future__ import annotations

import importlib
import pathlib

from clawops.hypermemory import HypermemoryEngine, load_config
from clawops.hypermemory.services import (
    BackendService,
    CanonicalStoreService,
    IndexingService,
    IndexService,
    QueryService,
    VerificationService,
)
from tests.utils.helpers.hypermemory import build_workspace, write_hypermemory_config
from tests.utils.helpers.repo import REPO_ROOT


def test_hypermemory_service_modules_exist() -> None:
    for module_name in (
        "clawops.hypermemory.contracts",
        "clawops.hypermemory.services.indexing_service",
        "clawops.hypermemory.services.query_service",
        "clawops.hypermemory.services.verification_service",
    ):
        module = importlib.import_module(module_name)
        assert module is not None


def test_hypermemory_engine_composes_service_objects(tmp_path: pathlib.Path) -> None:
    workspace = build_workspace(tmp_path)
    config_path = workspace / "hypermemory.sqlite.yaml"
    write_hypermemory_config(workspace, config_path)

    engine = HypermemoryEngine(load_config(config_path))

    assert isinstance(engine.index, IndexService)
    assert isinstance(engine.backend, BackendService)
    assert isinstance(engine.indexing, IndexingService)
    assert isinstance(engine.query, QueryService)
    assert isinstance(engine.verification, VerificationService)
    assert isinstance(engine.canonical_store, CanonicalStoreService)


def test_hypermemory_private_engine_package_is_removed() -> None:
    assert not (REPO_ROOT / "src/clawops/hypermemory/_engine").exists()


def test_hypermemory_engine_source_avoids_private_engine_imports() -> None:
    engine_source = (REPO_ROOT / "src/clawops/hypermemory/engine.py").read_text(encoding="utf-8")
    assert "._engine" not in engine_source
    assert "/_engine/" not in engine_source


def test_legacy_flat_engine_modules_are_removed() -> None:
    for relative_path in (
        "src/clawops/hypermemory/engine_backend.py",
        "src/clawops/hypermemory/engine_indexing.py",
        "src/clawops/hypermemory/engine_memory.py",
        "src/clawops/hypermemory/engine_query.py",
    ):
        assert not (REPO_ROOT / relative_path).exists()
