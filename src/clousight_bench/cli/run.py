"""The ``run`` and ``run-plan`` commands (+ live progress rendering)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clousight_bench.core.campaign.manifest import CampaignManifest

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from clousight_bench.cli._common import (
    _check_target,
    _exit_code,
    _load_config,
    _load_thresholds,
    _parse_params,
    _resolve_task_id,
    run_summary,
)
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec

_STATE_ICON = {
    "completed": "✓",
    "failed": "✗",
    "running": "▶",
    "pending": "·",
}


def _cmd_run(args: argparse.Namespace) -> int:
    target: dict[str, Any] = {}
    params: dict[str, Any] = {}
    cfg = _load_config(args.config)
    if cfg:
        target = _check_target(cfg.get("target", {}))
        params = cfg.get("params", {})
    params.update(_parse_params(args.param))

    spec = RunSpec(
        domain=args.domain,
        task_id=_resolve_task_id(args),
        platform=args.platform,
        target=target,
        params=params,
    )

    from clousight_bench.core.cost_notice import live_cost_notice

    notice = live_cost_notice(args.platform, task_count=1, allow_live=args.allow_live)
    if notice:
        print(notice, file=sys.stderr)

    if args.repeat != 1 or args.warmup != 0 or args.plan_id or args.resume:
        if getattr(args, "assert_thresholds", None):
            print(
                "warning: --assert applies to single runs only; it is ignored with "
                "--repeat/--warmup/--plan-id/--resume (the aggregate path is not gated).",
                file=sys.stderr,
            )
        from clousight_bench.ops.runplan import RunPlan, execute_plan

        plan = RunPlan(spec, repeat=args.repeat, warmup=args.warmup)
        aggregate = execute_plan(
            plan,
            results_dir=Path(args.results),
            enrich=not args.no_enrich,
            preflight=not args.skip_preflight,
            debug=args.debug,
            plan_id=args.plan_id,
            resume=args.resume,
            timeout_s=args.timeout,
            allow_live=args.allow_live,
            cost_budget=args.cost_budget,
        )
        print(aggregate.to_json())
        bad = sum(
            count
            for status, count in aggregate.status_counts.items()
            if status not in ("completed", "unsupported")
        )
        return 1 if bad else 0

    record = execute(
        spec,
        results_dir=Path(args.results),
        enrich=not args.no_enrich,
        preflight=not args.skip_preflight,
        debug=args.debug,
        timeout_s=args.timeout,
        allow_live=args.allow_live,
        cost_budget=args.cost_budget,
    )
    print(record.to_json())
    summary = run_summary(record)
    if summary:
        print(summary, file=sys.stderr)
    code = _exit_code(record)
    # Optional CI gate: fail the exit code if any measurement misses its threshold.
    if getattr(args, "assert_thresholds", None):
        from clousight_bench.core.thresholds import check_thresholds

        thresholds = _load_thresholds(args.assert_thresholds)
        failures = check_thresholds(record.measurements, thresholds)
        if failures:
            print("threshold(s) not met:\n  " + "\n  ".join(failures), file=sys.stderr)
            return code or 1
        print(f"all {len(thresholds)} threshold(s) met", file=sys.stderr)
    return code


def _cmd_run_plan(args: argparse.Namespace) -> int:
    import os

    if getattr(args, "mode", "dev") == "prod":
        print(
            "run-plan is dev-only (local development). For prod (production benchmarking) use: "
            "csbench submit <plan> --config <cfg>",
            file=sys.stderr,
        )
        return 2

    import yaml as _yaml

    from clousight_bench.core.campaign.manifest import (
        CampaignManifest,
        TaskProgress,
        new_campaign_id,
        write_manifest,
    )
    from clousight_bench.core.schema import RunSpec
    from clousight_bench.ops.runplan import RunPlan, execute_plan

    plan_path = Path(args.plan_file)
    try:
        spec = _yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"error: cannot read plan file {plan_path}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(spec, dict):
        print("error: plan file must be a YAML mapping", file=sys.stderr)
        return 2

    domain = spec.get("domain")
    platform = spec.get("platform")
    if not domain or not platform:
        print("error: plan must have 'domain' and 'platform'", file=sys.stderr)
        return 2

    base_target = spec.get("target") or {}
    if args.config:
        cfg = _load_config(args.config)
        base_target = {**cfg.get("target", {}), **base_target}
    base_target = _check_target(base_target)

    task_specs = spec.get("tasks") or []
    if not task_specs:
        print("error: plan has no tasks", file=sys.stderr)
        return 2

    results_dir = Path(args.results)
    allow_live = args.allow_live or (os.environ.get("CSBENCH_ALLOW_LIVE", "") == "1")
    cost_budget = args.cost_budget or (
        float(os.environ["CSBENCH_COST_BUDGET"]) if "CSBENCH_COST_BUDGET" in os.environ else None
    )

    # Validate task ids up front so the manifest reflects the real work list.
    task_ids: list[str] = []
    for t in task_specs:
        task_id = t.get("task")
        if not task_id:
            print("error: task entry missing 'task' key", file=sys.stderr)
            return 2
        task_ids.append(task_id)

    from clousight_bench.core.cost_notice import live_cost_notice

    notice = live_cost_notice(platform, task_count=len(task_ids), allow_live=allow_live)
    if notice:
        print(notice, file=sys.stderr)

    manifest = CampaignManifest(
        campaign_id=new_campaign_id(),
        plan_file=str(plan_path),
        domain=domain,
        platform=platform,
        tasks=[TaskProgress(task_id=tid) for tid in task_ids],
    )
    write_manifest(results_dir, manifest)
    print(
        f"campaign {manifest.campaign_id}  ({manifest.total_tasks} task(s))  "
        f"progress: csbench progress --results {results_dir}"
    )

    from clousight_bench.core.plugin import campaign_probe_hook  # local import

    hook = None
    # "eci" is a deprecated alias for "ecs" (the carrier is now a stock ECS
    # instance); both request the per-campaign in-region probe carrier.
    if getattr(args, "probe", "") in ("ecs", "eci"):
        # Resolve the carrier by the target's PROVIDER (e.g. "aliyun"), NOT the
        # platform name (e.g. "aliyun-agentrun") — get_runtime_provider keys on
        # provider. A silent fallback to the in-process probe on a mismatch would
        # run LOCAL while the operator believes they ran in-region, so fail loudly.
        provider = str(base_target.get("provider") or platform)
        hook = campaign_probe_hook(provider)
        if hook is None:
            print(
                f"error: --probe {args.probe} requested but no probe carrier "
                f"is registered for provider '{provider}'",
                file=sys.stderr,
            )
            return 2

    ok = failed = 0
    try:
        if hook is not None:
            base_target.update(hook.start_campaign_probe(dict(base_target)))
        for t in task_specs:
            task_id = t.get("task")
            repeat = int(t.get("repeat", 1))
            warmup = int(t.get("warmup", 0))
            params = t.get("params") or {}
            run_spec = RunSpec(domain, task_id, platform, target=dict(base_target), params=params)
            plan = RunPlan(run_spec, repeat=repeat, warmup=warmup)
            print(f"running {task_id}  repeat={repeat}  warmup={warmup} ...")
            manifest.mark_running(task_id)
            write_manifest(results_dir, manifest)
            try:
                agg = execute_plan(
                    plan,
                    results_dir=results_dir,
                    allow_live=allow_live,
                    cost_budget=cost_budget,
                )
                status_str = "  ".join(f"{s}={n}" for s, n in sorted(agg.status_counts.items()))
                print(f"  ✓ {task_id}  plan={agg.plan_id}  {status_str}")
                manifest.mark_done(
                    task_id, status="completed", plan_id=agg.plan_id, status_counts=agg.status_counts
                )
                ok += 1
            except Exception as exc:
                print(f"  ✗ {task_id}  {exc}", file=sys.stderr)
                manifest.mark_done(task_id, status="failed", error=str(exc))
                failed += 1
            write_manifest(results_dir, manifest)
            if hook is not None:
                try:
                    hook.sync_probe_artifacts(results_dir)  # cadence: after each task
                except Exception:  # noqa: BLE001 — a sync hiccup must not fail the campaign
                    pass
    finally:
        if hook is not None:
            try:
                hook.sync_probe_artifacts(results_dir)  # final sync for late chunks
            finally:
                hook.stop_campaign_probe()  # interrupt-safe reap

    total = ok + failed
    print(f"\n{total} task(s): {ok} ok, {failed} failed")
    return 0 if failed == 0 else 1


def _fmt_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m{seconds % 60:02d}s"


def _render_progress(manifest: CampaignManifest) -> str:  # noqa: F821
    import time as _time

    from clousight_bench.core.campaign.manifest import TERMINAL_STATES

    done = sum(1 for t in manifest.tasks if t.status in TERMINAL_STATES)
    running = [t for t in manifest.tasks if t.status == "running"]
    lines = [
        f"campaign {manifest.campaign_id}  ({manifest.platform})",
        f"progress: {done}/{manifest.total_tasks} done"
        + (f"  ·  {len(running)} running" if running else "")
        + f"  ·  updated {manifest.updated_at}",
        "",
    ]
    for t in manifest.tasks:
        icon = _STATE_ICON.get(t.status, "?")
        if t.status == "running" and t.started_at:
            try:
                started = _time.mktime(_time.strptime(t.started_at, "%Y-%m-%dT%H:%M:%S%z"))
                elapsed = f"{_fmt_elapsed(_time.time() - started)} (running)"
            except (ValueError, OverflowError):
                elapsed = "running"
        else:
            elapsed = _fmt_elapsed(t.elapsed_s)
        counts = "  ".join(f"{s}={n}" for s, n in sorted(t.status_counts.items()))
        detail = t.error or counts
        lines.append(f"  {icon} {t.task_id:<8} {elapsed:>14}   {detail}")
        jp = t.job_progress or {}
        if t.status == "running" and jp:
            completed = int(jp.get("completed", 0))
            total = int(jp.get("total", 0))
            phase = str(jp.get("phase", ""))
            pct = f" ({completed * 100 // total}%)" if total else ""
            lines.append(f"        └ {phase} {completed}/{total}{pct}")
        if t.chunk_refs:
            lines.append(f"        └ {len(t.chunk_refs)} chunk(s) in OSS")
    return "\n".join(lines)


def _cmd_progress(args: argparse.Namespace) -> int:
    import time as _time

    from clousight_bench.core.campaign.manifest import (
        CampaignManifest,  # noqa: F401  (used by _render_progress annotation)
        load_manifest,
        manifest_path,
    )
    from clousight_bench.core.campaign.manifest import (
        latest_manifest as _latest,
    )

    results_dir = Path(args.results)
    if args.campaign:
        path = manifest_path(results_dir, args.campaign)
        if not path.exists():
            print(f"error: no campaign {args.campaign!r} under {results_dir}", file=sys.stderr)
            return 2
    else:
        latest = _latest(results_dir)
        if latest is None:
            print(f"error: no campaigns found under {results_dir} — has a run-plan started?", file=sys.stderr)
            return 2
        path = latest

    def _print_once() -> CampaignManifest:  # noqa: F821
        manifest = load_manifest(path)
        if args.json:
            print(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(_render_progress(manifest))
        return manifest

    if not args.watch:
        _print_once()
        return 0

    from clousight_bench.core.campaign.manifest import TERMINAL_STATES

    try:
        while True:
            print("\033[2J\033[H", end="")  # clear screen, cursor home
            manifest = _print_once()
            if all(t.status in TERMINAL_STATES for t in manifest.tasks):
                break
            _time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    return 0
