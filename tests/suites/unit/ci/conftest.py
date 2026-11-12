"""Keep the CI-harness unit tests independent of a live fresh-host job's environment."""

from __future__ import annotations

import os

import pytest

from tests.plugins.infrastructure.context import TestContext

# A fresh-host job exports its budget, context, and runtime selection under this prefix (for
# example when `clawops baseline verify` runs the repository tests inside a feature lane).
_HARNESS_ENV_PREFIX = "FRESH_HOST_"


@pytest.fixture(autouse=True)
def _isolate_from_fresh_host_job_env(test_context: TestContext) -> None:
    for key in [key for key in os.environ if key.startswith(_HARNESS_ENV_PREFIX)]:
        test_context.env.remove(key)
