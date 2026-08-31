"""csbench: the Clousight Bench command line.

    csbench list                                        # installed domains / tasks / platforms
    csbench run --domain agent-runtime --benchmark swe-bench \
            --platform local-sim [--config cfg.yaml] [--param k=v ...] [--debug]
    csbench init aws [--domain agent-runtime] [--out .]  # scaffold private config + .env.example
    csbench doctor --config x.local.yaml                 # preflight: creds + connectivity

The command handlers live in submodules (run / results / ops / prod); ``app``
holds the parser + dispatch. Names re-exported here are the stable import surface
used by the ``csbench`` entry point and the test suite.
"""

from __future__ import annotations

# Re-exported so `from clousight_bench.cli import <name>` (and `cli.<name>`) keep
# working for the test suite after the split into submodules — the back-compat
# import surface, not new public API (hence not in __all__).
from clousight_bench.cli._common import _check_target, _load_config  # noqa: F401
from clousight_bench.cli.app import main
from clousight_bench.cli.prod import _controller_extra_deps  # noqa: F401
from clousight_bench.cli.results import _cmd_verify  # noqa: F401
from clousight_bench.cli.run import (  # noqa: F401
    _cmd_progress,
    _cmd_run_plan,
    _render_progress,
)

__all__ = ["main"]
