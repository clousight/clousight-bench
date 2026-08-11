from clousight_bench.domains.agent_runtime.dataplane_dispatch import PROBE_NAMES

from clousight_bench.domains.agent_runtime.probe.server import build_default_runner


def test_default_runner_registers_every_data_plane_probe():
    # The remote probe registry must match the open-core single source of truth
    # (PROBE_NAMES). No local hardcoded copy — that was the A1 drift risk.
    runner = build_default_runner()
    assert set(runner._probes) == set(PROBE_NAMES)
