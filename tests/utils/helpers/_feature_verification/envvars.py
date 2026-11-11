"""Environment variables that carry lane state from ``run-lane`` into the pytest session."""

from typing import Final

LANE_ENV: Final[str] = "FEATURE_VERIFICATION_LANE"
CONTEXT_ENV: Final[str] = "FEATURE_VERIFICATION_CONTEXT"
RESULTS_ENV: Final[str] = "FEATURE_VERIFICATION_RESULTS"
WORK_DIR_ENV: Final[str] = "FEATURE_VERIFICATION_WORK_DIR"
FAKE_MODEL_DIR_ENV: Final[str] = "FEATURE_VERIFICATION_FAKE_MODEL_DIR"
MANIFEST_ENV: Final[str] = "FEATURE_VERIFICATION_MANIFEST"
