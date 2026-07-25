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

EVIDENCE_LAYERS: tuple[str, ...] = ("A", "B", "C", "D")
SEVERITIES: tuple[str, ...] = ("info", "warning", "critical")


class ObservationError(ValueError):
    """An observation bundle or scored result violates the Task contract."""


@dataclass
class Measurement:
    """One scored number or label, with the evidence that backs it."""

    value: Any
    unit: str
    evidence: str
    aggregation: str = ""
    sample_count: int | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.evidence not in EVIDENCE_LAYERS:
            raise ObservationError(
                f"evidence must be one of {EVIDENCE_LAYERS}, got {self.evidence!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "value": self.value,
            "unit": self.unit,
            "evidence": self.evidence,
        }
        if self.aggregation:
            out["aggregation"] = self.aggregation
        if self.sample_count is not None:
            out["sample_count"] = self.sample_count
        if self.notes:
            out["notes"] = self.notes
        return out


@dataclass
class Finding:
    """A stable, machine-readable statement about what the run showed."""

    code: str
    severity: str
    summary: str
    evidence: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code:
            raise ObservationError("finding code must be a stable, non-empty string")
        if self.severity not in SEVERITIES:
            raise ObservationError(
                f"severity must be one of {SEVERITIES}, got {self.severity!r}"
            )
        if self.evidence not in EVIDENCE_LAYERS:
            raise ObservationError(
                f"evidence must be one of {EVIDENCE_LAYERS}, got {self.evidence!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "summary": self.summary,
            "evidence": self.evidence,
            "details": self.details,
        }


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


def collect(bundle: ObservationBundle) -> ObservationBundle:
    """COLLECT: prove the raw bundle is well formed and canonically encodable."""
    if not isinstance(bundle, ObservationBundle):
        raise ObservationError(
            f"execute() must return an ObservationBundle, got {type(bundle).__name__}"
        )
    canonical_json(bundle.to_dict())  # raises CanonicalJSONError on NaN / bad types
    for name, points in bundle.series.items():
        for point in points:
            if not (isinstance(point, (list, tuple)) and len(point) == 2):
                raise ObservationError(
                    f"series {name!r} points must be [t, value] pairs, got {point!r}"
                )
    for artifact in bundle.artifacts:
        missing = {"kind", "media", "sha256"} - set(artifact)
        if missing:
            raise ObservationError(
                f"artifact missing key(s) {sorted(missing)}: {artifact!r}"
            )
        if "path" not in artifact and "uri" not in artifact:
            raise ObservationError(
                f"artifact needs a path or uri pointer: {artifact!r}"
            )
    return bundle
