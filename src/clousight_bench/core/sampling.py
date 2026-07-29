"""HighFreqSampler: emit ``sample`` protocol events at a fixed interval.

Part of the open reference toolkit so any series-producing dimension is
reproducible: a workload wraps a value-producing callback and prints one JSONL
``sample`` event per tick to stdout, which the WorkloadEngine reads and (with
the ``[store]`` extra) externalises to ``series.parquet``. The measurement
itself is the callback; this class only owns the protocol, so richer probes
(GPU utilisation, token-level cost, cold-start decomposition) plug in without
forking it.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable


class HighFreqSampler:
    def __init__(self, series_name: str, interval_s: float = 0.01) -> None:
        self.series_name = series_name
        self.interval_s = interval_s

    def collect(self, callback: Callable[[], float], count: int) -> None:
        """Call ``callback`` ``count`` times, emitting one ``sample`` event each tick."""
        for _ in range(count):
            value = float(callback())
            event = {"type": "sample", "series": self.series_name, "t": time.time(), "value": value}
            print(json.dumps(event), flush=True)
            if self.interval_s:
                time.sleep(self.interval_s)
