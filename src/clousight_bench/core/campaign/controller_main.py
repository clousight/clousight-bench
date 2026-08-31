"""cb-controller — the on-ECS entrypoint for the ecs prod profile.

Runs on the ephemeral controller instance. Claims the campaign, drives the
serial orchestration loop (wrapping ``core.orchestrator.execute`` per task), and
lets the self-destruct watchdog reap the run (runtimes + NAT + self) on
completion/timeout/stop.

``build`` is a factory seam that wires everything from an env dict + an
``BlobStore`` WITHOUT running, so it is unit-testable with no cloud. ``main`` is
the thin live entrypoint (Oss2Client via the instance metadata role).
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clousight_bench.core.blobstore import BlobStore
from clousight_bench.core.campaign.channel import CampaignChannel
from clousight_bench.core.campaign.controller import CampaignController, RunTask, TaskOutcome
from clousight_bench.core.campaign.spec import DEFAULT_WATCHDOG_TIMEOUT_S
from clousight_bench.core.campaign.watchdog import SelfDestructWatchdog
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.resource_ledger import LEDGER_FILE, ResourceLedger, live_runtimes_from_ledger
from clousight_bench.core.schema import RunSpec

if TYPE_CHECKING:  # composition root: the concrete reaper is provider code
    from clousight_bench.domains.agent_runtime.controller_reaper import RestrictedReaper

_OK_STATUSES = ("completed", "unsupported")


def build_run_task(platform: str, results_dir: str | Path, *, allow_live: bool = True) -> RunTask:
    """A RunTask that runs one task through the full lifecycle and returns its
    serialized result (JSON + optional parquet sidecar) as a TaskOutcome.

    Per-task params (``LaunchSpec.tasks[].params``) overlay the campaign-wide
    ``spec.params``; the campaign ``cost_budget`` is forwarded into ``execute``
    (in-region runs are live by design — the operator gated at submit time)."""
    rdir = Path(results_dir)

    def run_task(task_id: str, spec: Any) -> TaskOutcome:
        run_spec = RunSpec(
            domain="agent-runtime",
            task_id=task_id,
            platform=platform,
            target=dict(spec.target),
            params={**dict(spec.params), **spec.task_params(task_id)},
        )
        record = execute(run_spec, results_dir=rdir, allow_live=allow_live, cost_budget=spec.cost_budget)
        result_json = record.to_json().encode("utf-8")
        sidecar = (
            rdir / record.identity.domain / record.identity.adapter / record.run.run_id / "series.parquet"
        )
        parquet = sidecar.read_bytes() if sidecar.exists() else None
        ok = record.status in _OK_STATUSES
        error = None if ok else (record.status if not record.errors else str(record.errors[0]))
        return TaskOutcome(
            task_id=task_id,
            ok=ok,
            result_json=result_json,
            series_parquet=parquet,
            error=error,
            run_id=record.run.run_id,
        )

    return run_task


def _ledger_bytes_reader(results_dir: str | Path) -> Callable[[], bytes]:
    path = Path(results_dir) / LEDGER_FILE
    return lambda: path.read_bytes() if path.exists() else b""


def build_reaper(
    env: dict[str, str],
    *,
    results_dir: str | Path,
    instance_id: str | None = None,
    live_runtimes: Callable[[], list[str]] | None = None,
    delete_runtime: Callable[[str], None] | None = None,
    delete_nat: Callable[[], None] | None = None,
    delete_self: Callable[[str], None] | None = None,
    log: Callable[[str], None] = lambda _m: None,
) -> RestrictedReaper:
    """Compose the self-destruct reaper: runtimes (from the ledger) → NAT → self.

    The live delete callables + the self-instance-id lookup come from the
    resolved runtime provider's :class:`~clousight_bench.core.plugin.ControllerReaperSpec`
    (``target``/plan-derived provider, ``CB_PLATFORM`` env) — so ``core`` holds no
    cloud SDK. Any of them can be overridden by an injected seam (tests win over
    the provider defaults). ``log`` (e.g. ``channel.append_log``) surfaces the
    live NAT-delete steps to OSS.

    Degrades gracefully: if no runtime provider is installed / the hook returns
    None (open-core ships none), the unfilled deleters become no-ops that log —
    the controller then leaves teardown to the local ``csbench teardown``
    backstop, matching ``main()``'s reaper-build try/except. This is NOT the
    fail-loud of ``csbench submit`` (user-initiated, fixable config) — the reaper
    runs unattended at teardown and must survive a provider-less environment.
    """
    # Composition root: this binary RUNS on the driver host, so wiring the
    # aliyun-specific reaper here (lazily) is deliberate — core modules other
    # than this entrypoint must not import provider code.
    from clousight_bench.core.credentials import infer_provider
    from clousight_bench.core.registry import get_runtime_provider
    from clousight_bench.domains.agent_runtime.controller_reaper import RestrictedReaper

    region = env.get("CB_REGION", "cn-hangzhou")
    ledger_dir = Path(results_dir)
    lr = live_runtimes or (lambda: live_runtimes_from_ledger(ResourceLedger(ledger_dir)))

    # Resolve the provider's live delete callables ONLY when a default is
    # actually needed — an all-injected call (the test path) never touches the
    # registry or the provider SDK.
    spec: Any | None = None
    if delete_runtime is None or delete_nat is None or delete_self is None or instance_id is None:
        provider = infer_provider({}, env.get("CB_PLATFORM"))
        plugin = get_runtime_provider(provider)
        # Direct call — controller_reaper_spec is a concrete ABC method that
        # defaults to None (mirrors prod_submit's plugin.controller_tf_spec()).
        # A None spec below falls back to _noop_del/empty iid, and an empty
        # instance-id only ever reaches a no-op deleter.
        spec = plugin.controller_reaper_spec(region, log) if plugin is not None else None

    def _noop_del(*_a: Any) -> None:
        log("reaper: no runtime provider reaper wired; leaving teardown to local backstop")

    iid = instance_id
    if iid is None:
        iid = spec.self_instance_id() if spec is not None else ""
    return RestrictedReaper(
        live_runtimes=lr,
        delete_runtime=delete_runtime or (spec.delete_runtime if spec is not None else _noop_del),
        delete_nat=delete_nat or (spec.delete_nat if spec is not None else _noop_del),
        delete_self=delete_self or (spec.delete_self if spec is not None else _noop_del),
        self_instance_id=iid,
    )


def build(
    env: dict[str, str],
    store: BlobStore,
    *,
    run_task: RunTask | None = None,
    reaper: Any | None = None,
    now: Callable[[], float] = time.time,
) -> tuple[CampaignController, SelfDestructWatchdog]:
    """Wire the controller + watchdog from env + the blob store. No side effects."""
    campaign_id = env["CB_CAMPAIGN_ID"]
    results_dir = env.get("CB_RESULTS_DIR", "/var/lib/cb/results")
    # Aliyun-carrier fallback: the ECS carrier's cloud-init always exports
    # CB_PLATFORM (aliyun/ecs_carrier.py:build_controller_user_data), so this
    # default is only reached when a caller builds the controller without it —
    # i.e. a misconfig or a unit test. Kept (not fail-loud) because build() is
    # constructed env-lite in tests; the reaper-side provider resolution already
    # degrades gracefully when the platform is absent/unknown.
    platform = env.get("CB_PLATFORM", "aliyun-agentrun")
    channel = CampaignChannel(store, campaign_id, now=now)

    rt = run_task or build_run_task(platform, results_dir)
    controller = CampaignController(channel, rt, now=now, ledger_bytes=_ledger_bytes_reader(results_dir))

    spec = channel.read_launch()
    timeout_s = spec.watchdog_timeout_s if spec else DEFAULT_WATCHDOG_TIMEOUT_S

    def _reap() -> Any:
        # Surface RestrictedReaper's collected best-effort errors to OSS (it runs
        # delete_nat BEFORE delete_self, so this write lands before the box dies).
        assert reaper is not None  # only wired as the reap callback when non-None (see below)
        errs = reaper.reap()
        if errs:
            with contextlib.suppress(Exception):
                channel.append_log("reaper errors:\n" + "\n".join(str(e) for e in errs))
        return errs

    reap = _reap if reaper is not None else (lambda: None)
    watchdog = SelfDestructWatchdog(channel, reap=reap, timeout_s=timeout_s, now=now)
    return controller, watchdog


def main() -> int:  # pragma: no cover - live entrypoint, exercised by the smoke runbook
    import traceback

    from clousight_bench.domains.agent_runtime.probe.oss_client import EcsRamRoleOssClient

    env = dict(os.environ)
    # In-region controller reads/writes OSS over the VPC-internal endpoint. Creds
    # come from THIS instance's RAM role read straight from the ECS metadata
    # service (requests-only, 5s timeout) — NOT the provider's default credential
    # chain (Oss2Client), whose ECS-metadata provider blocks silently with no
    # timeout when it can't resolve, hanging the controller before its first
    # OSS write (observed live 2026-08-15: zero boot markers, no traceback).
    store = EcsRamRoleOssClient(env["CB_OSS_BUCKET"], env.get("CB_REGION", "cn-hangzhou"))
    channel = CampaignChannel(store, env["CB_CAMPAIGN_ID"])
    # Boot markers to OSS: the controller's stdout/stderr don't reach the serial
    # console, so log progress here — a `csbench logs` shows exactly how far it got.
    try:
        channel.append_log("controller boot: python+import+oss OK")
        if not channel.claim():
            channel.append_log("campaign already claimed — exiting")
            return 0  # another controller already owns this campaign
        channel.append_log("claimed campaign; building controller+watchdog")
        # Self-destruct reaper: on watchdog-terminal it deletes residual runtimes
        # → NAT → this instance (last), so a finished/timed-out run leaves nothing
        # even if the laptop is off. Best-effort to build — if the metadata/SDK
        # wiring fails we log it and fall back to a noop reap rather than refuse to
        # run the campaign (the local `csbench teardown` is the backstop).
        reaper: Any | None = None
        try:
            results_dir = env.get("CB_RESULTS_DIR", "/var/lib/cb/results")
            reaper = build_reaper(env, results_dir=results_dir, log=channel.append_log)
            channel.append_log(f"reaper armed (self={reaper._self_instance_id})")
        except Exception as exc:  # noqa: BLE001 - never let reaper-build block the run
            channel.append_log(f"reaper build failed ({exc!r}); teardown falls to local backstop")
        controller, watchdog = build(env, store, reaper=reaper)
        channel.append_log("built; starting orchestration loop + watchdog")
        start = time.time()
        threading.Thread(target=controller.run, daemon=True).start()
        reason = watchdog.run_until_terminal(start)
        channel.append_log(f"watchdog terminal: {reason}")
        return 0
    except Exception:
        with contextlib.suppress(Exception):
            channel.append_log("CONTROLLER FATAL:\n" + traceback.format_exc()[-3000:])
        raise


if __name__ == "__main__":  # pragma: no cover - `python -m clousight_bench.core.campaign.controller_main`
    # cloud-init boots the controller via `python3.11 -m ...`; without this guard
    # that only imports the module and exits WITHOUT calling main() (observed live
    # 2026-08-15: controller wrote zero OSS, no stdout — main() never ran).
    raise SystemExit(main())
