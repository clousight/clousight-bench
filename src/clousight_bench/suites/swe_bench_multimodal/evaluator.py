"""Official SWE-bench Multimodal evaluator plugin.

Registered via the ``clousight_bench.evaluators`` entry-point group as
``official-swe-multimodal-evaluator``.  SWE-bench Multimodal's upstream harness
report shape is identical to Verified's, so this evaluator only retargets the
suite id (which also drives the ``swe-bench-multimodal.`` measurement namespace)
— all parsing logic is inherited from :class:`OfficialSweEvaluator`.
"""

from __future__ import annotations

from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator


class OfficialSweMultimodalEvaluator(OfficialSweEvaluator):
    """Evaluate SWE-bench Multimodal runs — emits ``swe-bench-multimodal.`` measurements."""

    evaluator_id = "official-swe-multimodal-evaluator"
    suite_id = "swe-bench-multimodal"
