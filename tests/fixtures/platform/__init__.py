"""Platform-facing shared pytest fixture plugins."""

pytest_plugins = (
    "tests.fixtures.platform.journal",
    "tests.fixtures.platform.network",
    "tests.fixtures.platform.observability",
    "tests.fixtures.platform.policy",
)
