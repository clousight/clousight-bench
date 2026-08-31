"""The two halves of a Task: raw observations and the scored result.

``ObservationBundle`` is what ``Task.execute`` produces: raw, replayable
evidence with no conclusion in it. ``TaskResult`` is what ``Task.score``
derives from a bundle, and only ``score`` is allowed to draw conclusions. The
split is what makes a historical observation re-scorable when a scorer is
fixed, and it is why ``score`` never touches a cloud.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from clousight_bench.core.canonical import canonical_json

REPRODUCIBILITY_CLASSES: tuple[str, ...] = ("deterministic", "environmental", "judge-based")
SEVERITIES: tuple[str, ...] = ("info", "warning", "critical")
# Per-item / per-metric outcome states (the 4-state model borrowed from
# DeepEval). ``fail`` is a RESULT (the SUT underperformed), ``skip`` means a
# required input/capability was absent (not scored), ``error`` means the metric
# itself crashed (a bug, isolated per-metric so one failure never voids a run).
ITEM_SCORE_STATUSES: tuple[str, ...] = ("ok", "fail", "skip", "error")


class ObservationError(ValueError):
    """An observation bundle or scored result violates the Task contract."""


@dataclass
class Measurement:
    """One scored number or label.

    ``reproducibility_class`` records how a re-run would behave: ``deterministic``
    (re-runs identically), ``environmental`` (a timing/cost/resource number that
    drifts with the environment) or ``judge-based`` (an LLM-as-judge score). It
    may be left blank (unclassified). ``official`` marks whether the number is a
    published, comparable result.
    """

    value: Any
    unit: str
    reproducibility_class: str = ""
    official: bool = True
    aggregation: str = ""
    sample_count: int | None = None
    notes: str = ""
    ci: tuple[float, float] | None = None  # optional (lo, hi) confidence interval

    def __post_init__(self) -> None:
        if self.reproducibility_class and self.reproducibility_class not in REPRODUCIBILITY_CLASSES:
            raise ObservationError(
                f"reproducibility_class must be one of {REPRODUCIBILITY_CLASSES} or empty, "
                f"got {self.reproducibility_class!r}"
            )
        if self.ci is not None:
            lo, hi = self.ci  # unpacking rejects any non-2-tuple with a clear error
            if lo > hi:
                raise ObservationError(f"ci lower bound {lo} exceeds upper bound {hi}")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "value": self.value,
            "unit": self.unit,
        }
        if self.reproducibility_class:
            out["reproducibility_class"] = self.reproducibility_class
        out["official"] = self.official
        if self.aggregation:
            out["aggregation"] = self.aggregation
        if self.sample_count is not None:
            out["sample_count"] = self.sample_count
        if self.notes:
            out["notes"] = self.notes
        if self.ci is not None:
            out["ci"] = [self.ci[0], self.ci[1]]
        return out


@dataclass
class Finding:
    """A stable, machine-readable statement about what the run showed."""

    code: str
    severity: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code:
            raise ObservationError("finding code must be a stable, non-empty string")
        if self.severity not in SEVERITIES:
            raise ObservationError(f"severity must be one of {SEVERITIES}, got {self.severity!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "summary": self.summary,
            "details": self.details,
        }


@dataclass
class ItemScore:
    """One metric's score for one item — the atom that Measurements aggregate.

    ``status`` is the 4-state outcome (see ``ITEM_SCORE_STATUSES``): a ``fail`` is
    a real result, ``skip``/``error`` are not scored / a bug. ``reason`` carries a
    judge rationale or diagnostic; ``error`` is set only when ``status=="error"``.
    """

    metric: str
    value: Any
    status: str = "ok"
    reason: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        if not self.metric:
            raise ObservationError("ItemScore.metric must be a non-empty metric id")
        if self.status not in ITEM_SCORE_STATUSES:
            raise ObservationError(f"status must be one of {ITEM_SCORE_STATUSES}, got {self.status!r}")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"metric": self.metric, "value": self.value, "status": self.status}
        if self.reason:
            out["reason"] = self.reason
        if self.error:
            out["error"] = self.error
        return out


@dataclass
class ItemResult:
    """First-class, portable, re-scorable per-example evidence.

    ``input``/``output``/``reference`` may hold the value directly or a pointer
    ``{"$artifact": "<manifest-key-or-relpath>"}`` for large blobs resolved
    against the staged artifacts dir, so the substrate scales to swe-bench-size
    patches without bloating the record.
    """

    item_id: str
    group: str = ""
    input: Any = None
    output: Any = None
    reference: Any = None
    scores: list[ItemScore] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    attrs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.item_id:
            raise ObservationError("ItemResult.item_id must be a stable, non-empty string")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"item_id": self.item_id, "scores": [s.to_dict() for s in self.scores]}
        if self.group:
            out["group"] = self.group
        for key in ("input", "output", "reference"):
            val = getattr(self, key)
            if val is not None:
                out[key] = val
        if self.usage:
            out["usage"] = self.usage
        if self.attrs:
            out["attrs"] = self.attrs
        return out


@dataclass
class ObservationBundle:
    """Raw, replayable evidence. Never a conclusion."""

    observations: dict[str, Any] = field(default_factory=dict)
    series: dict[str, list] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "observations": self.observations,
            "series": self.series,
            "artifacts": self.artifacts,
        }


@dataclass
class TaskResult:
    """What ``Task.score`` derives from an ObservationBundle."""

    measurements: dict[str, Measurement] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    items: list[ItemResult] = field(default_factory=list)
    notes: str = ""
    task_revision: str = ""
    scorer_revision: str = ""
    unsupported: bool = False


class TaskExecutionError(RuntimeError):
    """EXECUTE failed after producing observations that must remain auditable."""

    def __init__(
        self,
        message: str,
        *,
        observations: ObservationBundle,
        code: str = "task_execute_failed",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.observations = observations
        self.code = code
        self.retryable = retryable


def validate_observation_bundle(bundle: ObservationBundle) -> None:
    """Raise when a full or partial observation bundle is unsafe to record."""
    if not isinstance(bundle, ObservationBundle):
        raise ObservationError(f"execute() must return an ObservationBundle, got {type(bundle).__name__}")
    if not isinstance(bundle.observations, dict):
        raise ObservationError(f"observations must be a dict, got {type(bundle.observations).__name__}")
    if not isinstance(bundle.series, dict):
        raise ObservationError(f"series must be a dict, got {type(bundle.series).__name__}")
    if not isinstance(bundle.artifacts, list):
        raise ObservationError(f"artifacts must be a list, got {type(bundle.artifacts).__name__}")
    for name, points in bundle.series.items():
        if not isinstance(name, str) or not isinstance(points, list):
            raise ObservationError("series names must be strings and values must be lists")
        for point in points:
            if not (isinstance(point, (list, tuple)) and len(point) == 2):
                raise ObservationError(f"series {name!r} points must be [t, value] pairs, got {point!r}")
    for artifact in bundle.artifacts:
        if not isinstance(artifact, dict):
            raise ObservationError(f"artifacts must contain dicts, got {type(artifact).__name__}")
        missing = {"kind", "media", "sha256"} - set(artifact)
        if missing:
            raise ObservationError(f"artifact missing key(s) {sorted(missing)}: {artifact!r}")
        if "path" not in artifact and "uri" not in artifact:
            raise ObservationError(f"artifact needs a path or uri pointer: {artifact!r}")
    canonical_json(bundle.to_dict())  # raises CanonicalJSONError on NaN / bad types


def collect(bundle: ObservationBundle) -> ObservationBundle:
    """COLLECT: prove the raw bundle is well formed and canonically encodable."""
    validate_observation_bundle(bundle)
    return bundle
