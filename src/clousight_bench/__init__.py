"""Clousight Bench: reproducible, reproducibility-classed benchmarking for cloud products."""

RUNNER_VERSION = "0.4.0"  # keep in sync with pyproject [project].version (CI wheel-smoke enforces)

# The result contract readers negotiate on (Phase 1B replaced schema "1.0").
# Re-exported, never re-declared: the record module owns the number it stamps
# and validates, so the package can never advertise a version the writer does
# not produce. core.record imports nothing from this package, so this cannot
# become an import cycle.
from clousight_bench.core.record import SCHEMA_VERSION  # noqa: E402

RESULT_SCHEMA_VERSION = SCHEMA_VERSION

# Temporary compatibility contract for the plugin surface.
# Phase 1D replaces this with API-range negotiation.
PLUGIN_API_VERSION = "1.0"

__version__ = RUNNER_VERSION
