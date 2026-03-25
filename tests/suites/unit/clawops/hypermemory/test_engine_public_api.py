from __future__ import annotations

from clawops.hypermemory import HypermemoryEngine as ReexportedEngine
from clawops.hypermemory.engine import HypermemoryEngine


def test_hypermemory_engine_public_import_contract() -> None:
    assert ReexportedEngine is HypermemoryEngine
