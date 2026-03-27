"""Tracked patch lifecycle management integrated with the infrastructure runtime."""

from __future__ import annotations

import unittest.mock
from collections.abc import Mapping
from typing import Any

from tests.plugins.infrastructure.types import RuntimeTestContext


class PatchManager:
    """Start patches and register deterministic teardown with ``TestContext``."""

    def __init__(self, context: RuntimeTestContext) -> None:
        self._context = context
        self._patches: list[Any] = []

    def patch(self, target: str, **kwargs: Any) -> Any:
        """Patch a module attribute and register teardown."""
        patcher = unittest.mock.patch(target, **kwargs)
        mocked = patcher.start()
        self._context.register_patch_cleanup(f"patch:{target}", patcher.stop)
        self._patches.append(patcher)
        return mocked

    def patch_object(self, target: object, attribute: str, **kwargs: Any) -> Any:
        """Patch an object attribute and register teardown."""
        patcher = unittest.mock.patch.object(target, attribute, **kwargs)
        mocked = patcher.start()
        self._context.register_patch_cleanup(
            f"patch.object:{type(target).__name__}.{attribute}",
            patcher.stop,
        )
        self._patches.append(patcher)
        return mocked

    def patch_dict(self, target: Mapping[str, Any], values: Mapping[str, Any]) -> None:
        """Patch a mapping and register teardown."""
        patcher = unittest.mock.patch.dict(target, values)
        patcher.start()
        self._context.register_patch_cleanup(f"patch.dict:{id(target):#x}", patcher.stop)
        self._patches.append(patcher)
