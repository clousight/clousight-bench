"""Official TPC-DS QphDS@SF evaluator (pure function over the official.json artifact).

Maps the DS phase-machine output (Load / Power / TT1 / DM1 / TT2 / DM2 / ACID)
into namespaced ``tpc-ds.*`` :class:`Measurement` objects using the official
QphDS formula (:func:`clousight_bench.suites._tpc_official.metrics.qphds_at_size`).
Registered as ``official-tpcds-qphds-evaluator`` — the ``mode: official``
companion to ``official-tpcds-evaluator`` (reference path).

Every score carries ``official=True`` as a *provenance flag*, NOT an audit claim:
the composite notes ``unaudited`` — the query ordering and the data-maintenance
set are clousight-generated. Timing scores are ``environmental``; SF1 correctness
(pinned reference over the Power stream) and ACID pass/fail are ``deterministic``.
A missing/malformed section omits only the affected dimension — never raises.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clousight_bench.core.observation import Measurement
from clousight_bench.core.suite import Evaluator, RawArtifacts
from clousight_bench.suites._tpc_official import metrics

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_UNAUDITED = (
    "official TPC-DS QphDS formula; unaudited; ordering and data-maintenance set are clousight-generated"
)


class OfficialTpcdsQphdsEvaluator(Evaluator):
    """Evaluate a ``mode: official`` TPC-DS run's ``official.json`` into QphDS measurements."""

    evaluator_id = "official-tpcds-qphds-evaluator"
    official = True
    fixtures_dir: Path = _FIXTURES_DIR

    def supports(self, suite_id: str, product: str) -> bool:  # noqa: ARG002
        return suite_id == "tpc-ds"

    def _load_reference(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads((self.fixtures_dir / "reference" / "sf1_digests.json").read_text())
        except Exception:  # noqa: BLE001 - missing/broken reference just omits correctness
            return {}

    def evaluate(self, raw: RawArtifacts) -> dict[str, Measurement]:
        try:
            doc: dict[str, Any] = json.loads(raw.path("official").read_text())
        except Exception:  # noqa: BLE001 - no/broken official.json → nothing to score
            return {}
        if not isinstance(doc, dict):
            return {}

        out: dict[str, Measurement] = {}
        sf = _as_float(doc.get("scale_factor"))

        load_s = _as_float((doc.get("load") or {}).get("load_time_s"))
        if load_s is not None:
            out["tpc-ds.load_time_s"] = Measurement(
                value=load_s, unit="s", reproducibility_class="environmental", official=True
            )

        components = self._components(doc)
        if components is not None and sf is not None:
            t_power_s, t_tt1_s, t_tt2_s, t_dm1_s, t_dm2_s, num_queries = components
            streams = int(doc.get("streams") or 0)
            for key, value in (
                ("tpc-ds.power_test_s", t_power_s),
                ("tpc-ds.throughput_test_s", t_tt1_s + t_tt2_s),
                ("tpc-ds.maintenance_test_s", t_dm1_s + t_dm2_s),
            ):
                out[key] = Measurement(
                    value=value, unit="s", reproducibility_class="environmental", official=True
                )
            if streams > 0 and load_s is not None:
                try:
                    qphds = metrics.qphds_at_size(
                        scale_factor=sf,
                        num_streams=streams,
                        num_queries=num_queries,
                        t_power_s=t_power_s,
                        t_tt1_s=t_tt1_s,
                        t_tt2_s=t_tt2_s,
                        t_dm1_s=t_dm1_s,
                        t_dm2_s=t_dm2_s,
                        t_load_s=load_s,
                    )
                except ValueError:
                    qphds = None
                if qphds is not None:
                    out["tpc-ds.qphds_at_size"] = Measurement(
                        value=qphds,
                        unit="QphDS",
                        reproducibility_class="environmental",
                        official=True,
                        notes=_UNAUDITED,
                    )

        if sf == 1.0:
            self._add_correctness(out, doc)
        self._add_acid(out, doc)
        return out

    # ------------------------------------------------------------------ helpers
    def _components(self, doc: dict[str, Any]) -> tuple[float, float, float, float, float, int] | None:
        power = doc.get("power")
        if not isinstance(power, dict):
            return None
        queries = power.get("queries")
        if not isinstance(queries, list) or not queries:
            return None
        try:
            t_power_s = sum(float(q["interval_s"]) for q in queries)
        except (KeyError, TypeError, ValueError):
            return None
        tt1 = _as_float((doc.get("throughput1") or {}).get("elapsed_s"))
        tt2 = _as_float((doc.get("throughput2") or {}).get("elapsed_s"))
        dm1 = _as_float((doc.get("dm1") or {}).get("elapsed_s"))
        dm2 = _as_float((doc.get("dm2") or {}).get("elapsed_s"))
        if tt1 is None or tt2 is None or dm1 is None or dm2 is None:
            return None
        return t_power_s, tt1, tt2, dm1, dm2, len(queries)

    def _add_correctness(self, out: dict[str, Measurement], doc: dict[str, Any]) -> None:
        reference = self._load_reference()
        if not reference:
            return
        queries = (doc.get("power") or {}).get("queries") or []
        passed = 0
        counted = 0
        for q in queries:
            try:
                nr = str(int(q["query_nr"]))
                digest = q["result_digest"]
            except (KeyError, TypeError, ValueError):
                continue
            ref = reference.get(nr)
            if ref is None:
                continue
            counted += 1
            if digest == ref.get("result_digest"):
                passed += 1
        if counted > 0:
            out["tpc-ds.queries_passed"] = Measurement(
                value=passed / counted,
                unit="ratio",
                reproducibility_class="deterministic",
                official=True,
                sample_count=counted,
                notes="pinned-reference reproducibility vs duckdb tpcds SF1; not an audited TPC answer",
            )

    def _add_acid(self, out: dict[str, Measurement], doc: dict[str, Any]) -> None:
        acid = doc.get("acid")
        if not isinstance(acid, dict):
            return
        note = "best-effort adaptation of TPC-DS ACID tests; unaudited"
        for name in ("atomicity", "consistency", "isolation"):
            verdict = acid.get(name)
            if verdict in ("pass", "fail"):
                out[f"tpc-ds.acid_{name}"] = Measurement(
                    value=1.0 if verdict == "pass" else 0.0,
                    unit="pass",
                    reproducibility_class="deterministic",
                    official=True,
                    notes=note,
                )
        # consistency/durability "n/a" → intentionally omitted from the measurement set


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
