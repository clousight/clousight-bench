"""Clousight Bench: reproducible, evidence-graded benchmarking for cloud products."""

RUNNER_VERSION = "1.0.0"

# Plugin compatibility contract (SemVer). Commercial/3rd-party plugins pin this.
# Bump the MAJOR when a plugin-facing contract (schema fields, entry-point
# groups, enricher/store signatures) changes incompatibly.
PLUGIN_API_VERSION = "1.0"

__version__ = RUNNER_VERSION
