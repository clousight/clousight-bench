"""ReportRenderer contract: turn a ReportBundle into an output document."""

from __future__ import annotations

from clousight_bench.core.reporting.bundle import ReportBundle


class ReportRenderer:
    name: str = "abstract"
    output_suffix: str = ".html"

    def render(self, bundle: ReportBundle) -> str:
        raise NotImplementedError
