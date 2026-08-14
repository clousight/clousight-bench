"""SelfDestructWatchdog — decides when the controller must tear itself down.

Terminal conditions (any one trips it): the controller wrote a DONE/FAILED
marker, the campaign exceeded its ``watchdog_timeout_s`` wall-clock, or the
laptop wrote a stop sentinel. On the first terminal condition the watchdog calls
``reap`` EXACTLY ONCE (delete runtimes + NAT + self) and returns the reason.

``now``/``poll``/``sleep`` are injected so the loop is deterministic in tests.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from clousight_bench.domains.agent_runtime.probe.campaign_channel import CampaignChannel


class SelfDestructWatchdog:
    def __init__(
        self,
        channel: CampaignChannel,
        reap: Callable[[], None],
        timeout_s: float,
        *,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._ch = channel
        self._reap = reap
        self._timeout_s = timeout_s
        self._now = now
        self._reaped = False

    def should_stop(self, start_ts: float) -> str | None:
        if self._ch.is_done() is not None:
            return "done"
        if self._now() - start_ts > self._timeout_s:
            return "timeout"
        if self._ch.stop_requested():
            return "stop"
        return None

    def run_until_terminal(
        self,
        start_ts: float,
        *,
        poll: Callable[[], None] = lambda: None,
        sleep: Callable[[float], None] = time.sleep,
        interval_s: float = 15.0,
    ) -> str:
        """Poll until a terminal condition, then reap once and return the reason."""
        while True:
            poll()
            reason = self.should_stop(start_ts)
            if reason is not None:
                if not self._reaped:
                    self._reaped = True
                    self._reap()
                return reason
            sleep(interval_s)
