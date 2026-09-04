"""Engine-agnostic official TPC phase machine (QphH@Size pilot for TPC-H).

This subpackage holds the official-metric orchestration that is deliberately kept
free of any ``duckdb`` import: the phase machine (:mod:`phases`), refresh functions
(:mod:`refresh`), concurrent streams (:mod:`streams`), ACID probes (:mod:`acid`) and
the pure metric formulas (:mod:`metrics`) all take injected callables/handles. The
DuckDB-backed suite supplies those closures, so the same machinery is reused by
``tpc-ds`` (QphDS@Size) without change.

Numbers produced here reproduce the official TPC-H formulas but are **unaudited**:
no TPC membership, no audit, no priced full-disclosure report.
"""
