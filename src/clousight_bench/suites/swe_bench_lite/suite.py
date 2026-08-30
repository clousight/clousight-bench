"""SWE-bench Lite benchmark suite plugin.

Registers as the ``swe-bench-lite`` suite under the
``clousight_bench.benchmark_suites`` entry-point group.

SWE-bench Lite is the 300-instance curated subset of SWE-bench: the SAME
upstream harness (``python -m swebench.harness.run_evaluation``), the SAME
report shape, and the SAME agent-runtime plumbing as the flagship Verified
suite.  The only differences are the pinned HuggingFace split, the bundled
instance/artefact fixtures, and the ``swe-bench-lite`` measurement namespace —
so this suite is a thin subclass of :class:`SweBenchSuite` that overrides only
the identity/dataset-binding class attributes.

The real ``run()`` path (Docker + upstream harness) requires the optional
``[swebench]`` extra; all other paths work without it.  See the flagship
``suites/swe_bench/suite.py`` for the full method documentation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clousight_bench.suites.swe_bench.suite import SweBenchSuite

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Pinned HuggingFace revision for the SWE-bench Lite test split used by this
# plugin.  Real main commit of princeton-nlp/SWE-bench_Lite, verified 2026-08-30
# via https://huggingface.co/api/datasets/princeton-nlp/SWE-bench_Lite/refs
_HF_REVISION = "princeton-nlp/SWE-bench_Lite@6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2"


class SweBenchLiteSuite(SweBenchSuite):
    """SWE-bench Lite suite plugin — 300-instance curated subset.

    Everything (resolve/prepare/run/teardown/mock_artifacts) is inherited from
    :class:`SweBenchSuite`; only the dataset binding and fixtures differ.
    """

    suite_id: str = "swe-bench-lite"
    suite_version: str = _HF_REVISION
    fixtures_dir: Path = _FIXTURES_DIR
    dataset_name: str = "princeton-nlp/SWE-bench_Lite"
    split: str = "test"

    # Own instance-row cache — MUST be redeclared so the Lite fixtures are not
    # shadowed by (or shadowing) the base Verified cache via the MRO.
    _instances_full_cache: list[dict[str, Any]] | None = None
