"""Compatibility re-export for infrastructure environment management."""

from __future__ import annotations

from tests.plugins.infrastructure.environment import (
    FRAMEWORK_ENV_VARS,
    EnvironmentManager,
    register_env_addoption,
)
from tests.plugins.infrastructure.types import IsolationMode

__all__ = [
    "FRAMEWORK_ENV_VARS",
    "EnvironmentManager",
    "IsolationMode",
    "register_env_addoption",
]
