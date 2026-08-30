"""Official SWE-bench Lite evaluator plugin.

Registered via the ``clousight_bench.evaluators`` entry-point group as
``official-swe-lite-evaluator``.  SWE-bench Lite's upstream harness report shape
is identical to Verified's, so this evaluator only retargets the suite id (which
also drives the ``swe-bench-lite.`` measurement namespace) — all parsing logic
is inherited from :class:`OfficialSweEvaluator`.
"""

from __future__ import annotations

from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator


class OfficialSweLiteEvaluator(OfficialSweEvaluator):
    """Evaluate SWE-bench Lite runs — emits ``swe-bench-lite.`` measurements."""

    evaluator_id = "official-swe-lite-evaluator"
    suite_id = "swe-bench-lite"
