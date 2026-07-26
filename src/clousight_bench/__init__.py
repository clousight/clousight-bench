"""Clousight Bench: reproducible, evidence-graded benchmarking for cloud products."""

RUNNER_VERSION = "0.2.0"

# The result contract readers negotiate on. Phase 1B replaced schema "1.0".
RESULT_SCHEMA_VERSION = "0.2"

# Temporary compatibility contract for the plugin surface.
# Phase 1D replaces this with API-range negotiation.
PLUGIN_API_VERSION = "1.0"

__version__ = RUNNER_VERSION
