"""Campaign operations over the single-run eval core — the optional upper layer.

``ops`` holds what the "benchmark campaign" heritage needs beyond one run:
run-plans (``ops.runplan``) and cross-record analytics/rollups
(``ops.analytics``). The eval core (``clousight_bench.core``) never imports
this package; ops depends on core, never the reverse.
"""
