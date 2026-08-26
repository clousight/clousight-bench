"""The benchmark_suite / evaluator plugin contract.

A ``BenchmarkSuite`` drives a recognized suite's OWN upstream harness unmodified
and returns opaque ``RawArtifacts``. An ``Evaluator`` reads those artifacts (a
pure function — no cloud, no credentials) into ``Measurement``s. Core treats the
handles as opaque, reading only ``DatasetHandle.{version, digest}`` for the
benchmark fingerprint; only the paired evaluator understands the artifacts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clousight_bench.core.observation import Measurement

TARGET_MODES: tuple[str, ...] = ("endpoint", "runtime")
PLACEMENTS: tuple[str, ...] = ("local", "in_cloud")


@dataclass
class DatasetHandle:
    version: str
    digest: str
    payload: dict[str, Any] = field(default_factory=dict)  # suite-private


@dataclass
class EnvHandle:
    payload: dict[str, Any] = field(default_factory=dict)  # suite-private


@dataclass
class RawArtifacts:
    dir: Path
    manifest: dict[str, dict[str, Any]] = field(default_factory=dict)

    def path(self, name: str) -> Path:
        return self.dir / self.manifest[name]["path"]


@dataclass
class Target:
    mode: str
    mock: bool
    handle: Any = None
    region: str = ""
    endpoint: str = ""
    credentials_ref: str = ""

    def __post_init__(self) -> None:
        if self.mode not in TARGET_MODES:
            raise ValueError(f"Target.mode must be one of {TARGET_MODES}, got {self.mode!r}")


@dataclass
class DriverContext:
    placement: str

    def __post_init__(self) -> None:
        if self.placement not in PLACEMENTS:
            raise ValueError(f"DriverContext.placement must be one of {PLACEMENTS}, got {self.placement!r}")


class BenchmarkSuite(ABC):
    suite_id: str = "abstract"
    suite_version: str = "0"

    @abstractmethod
    def resolve(self, cfg: dict[str, Any], assets: Any) -> DatasetHandle: ...
    @abstractmethod
    def prepare(self, target: Target, dataset: DatasetHandle, driver: DriverContext) -> EnvHandle: ...
    @abstractmethod
    def run(self, target: Target, env: EnvHandle, driver: DriverContext) -> RawArtifacts: ...
    def teardown(self, env: EnvHandle) -> None:
        return None

    @abstractmethod
    def mock_artifacts(self, cfg: dict[str, Any]) -> RawArtifacts: ...


class Evaluator(ABC):
    evaluator_id: str = "abstract"
    official: bool = True

    @abstractmethod
    def supports(self, suite_id: str, product: str) -> bool: ...
    @abstractmethod
    def evaluate(self, raw: RawArtifacts) -> dict[str, Measurement]: ...
