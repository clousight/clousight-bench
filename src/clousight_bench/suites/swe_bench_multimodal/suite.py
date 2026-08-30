"""SWE-bench Multimodal benchmark suite plugin.

Registers as the ``swe-bench-multimodal`` suite under the
``clousight_bench.benchmark_suites`` entry-point group.

SWE-bench Multimodal augments SWE-bench task issues with images (screenshots,
diagrams) drawn from real JavaScript / visual-frontend repositories.  It reuses
the SAME upstream harness, report shape, and agent-runtime plumbing as the
flagship Verified suite, so this suite is a thin subclass of
:class:`SweBenchSuite` that overrides only the identity / dataset-binding class
attributes.  The multimodal payload rides on a 7th fixture field,
``image_assets`` (a JSON string of image URLs keyed by ``problem_statement`` /
``patch`` / ``test_patch``); :meth:`_load_instance` returns the full row so the
gated real-SUT path forwards the images to a multimodal agent.

**Split choice — the ``dev`` split, deliberately.**  The 510-instance ``test``
split is the public leaderboard and its GOLD PATCHES ARE HELD OUT
(``patch``/``test_patch`` are empty in the dataset); scoring it requires the
hosted ``sb-cli`` evaluation service (``api.swebench.com``).  The 102-instance
``dev`` split ships real gold patches, so it is the locally reproducible,
Docker-runnable path — matching how Verified/Lite work.  Wiring the hosted
test-split path is a documented future (gated) addition.

The real ``run()`` path (Docker + upstream harness) requires the optional
``[swebench]`` extra; all other paths work without it.  See the flagship
``suites/swe_bench/suite.py`` for full method documentation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clousight_bench.suites.swe_bench.suite import SweBenchSuite

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Pinned HuggingFace revision for the SWE-bench Multimodal split used by this
# plugin.  Real main commit of princeton-nlp/SWE-bench_Multimodal, verified
# 2026-08-30 via https://huggingface.co/api/datasets/princeton-nlp/SWE-bench_Multimodal/refs
_HF_REVISION = "princeton-nlp/SWE-bench_Multimodal@aa2db68940196b6b59ae3f577faa0c25157bdd50"


class SweBenchMultimodalSuite(SweBenchSuite):
    """SWE-bench Multimodal suite plugin — image-augmented JS/visual issues.

    Everything (resolve/prepare/run/teardown/mock_artifacts) is inherited from
    :class:`SweBenchSuite`; only the dataset binding and fixtures differ.  Runs
    on the ``dev`` split (public gold patches); the held-out ``test`` leaderboard
    split needs the hosted ``sb-cli`` service and is a future gated path.
    """

    suite_id: str = "swe-bench-multimodal"
    suite_version: str = _HF_REVISION
    fixtures_dir: Path = _FIXTURES_DIR
    dataset_name: str = "princeton-nlp/SWE-bench_Multimodal"
    split: str = "dev"

    # Own instance-row cache — MUST be redeclared so the Multimodal fixtures are
    # not shadowed by (or shadowing) the base Verified cache via the MRO.
    _instances_full_cache: list[dict[str, Any]] | None = None
