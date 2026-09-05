"""Clousight Bench: reproducible, reproducibility-classed benchmarking for cloud products.

Build on the framework via the stable public surface — import the plugin
contracts and data model from ``clousight_bench`` (or ``clousight_bench.api``),
never from ``clousight_bench.core.*`` (internal). See :mod:`clousight_bench.api`.
"""

RUNNER_VERSION = "0.6.0"  # keep in sync with pyproject [project].version (CI wheel-smoke enforces)

# The result contract readers negotiate on. Re-exported, never re-declared: the
# record module owns the number it stamps and validates, so the package can never
# advertise a version the writer does not produce. core.record imports nothing
# from this package, so this cannot become an import cycle.
from clousight_bench.core.record import SCHEMA_VERSION  # noqa: E402

RESULT_SCHEMA_VERSION = SCHEMA_VERSION

# Plugin-surface compatibility contract (SemVer). A plugin declares the range it
# needs via ``requires_plugin_api``; the registry enforces it at load
# (core.registry). MAJOR bumps only on a breaking change to the public plugin
# surface (the contracts re-exported from clousight_bench.api).
PLUGIN_API_VERSION = "3.0"

__version__ = RUNNER_VERSION

# The stable public surface (see clousight_bench.api). Imported here so
# `from clousight_bench import BenchmarkSuite, Evaluator, Metric, Measurement, ...`
# works without reaching into core.
from clousight_bench.api import *  # noqa: E402,F403
from clousight_bench.api import __all__ as _api_all  # noqa: E402

__all__ = [
    "RUNNER_VERSION",
    "RESULT_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "PLUGIN_API_VERSION",
    "__version__",
    *_api_all,
]
