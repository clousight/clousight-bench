"""csbench: the Clousight Bench command line.

    csbench list                                        # installed domains / tasks / platforms
    csbench run --domain agent-runtime --task suite:swe-bench \
            --platform local-sim [--config cfg.yaml] [--param k=v ...] [--debug]
    #   --repeat N --warmup W  -> run a plan and print a statistical aggregate
    #   results/publish-receipts.jsonl records publish attempts (append-only)
    csbench init aws [--domain agent-runtime] [--out .]  # scaffold private config + .env.example
    csbench doctor --config x.local.yaml                 # preflight: creds + connectivity
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clousight_bench.core.campaign import CampaignManifest

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

from clousight_bench.core.errors import (
    UnknownPlatformError,
    UnknownTaskError,
    UserInputError,
)
from clousight_bench.core.orchestrator import DEFAULT_RESULTS_DIR, execute
from clousight_bench.core.registry import load_domains
from clousight_bench.core.schema import RunSpec


def _cmd_list(args: argparse.Namespace) -> int:
    from clousight_bench.core.registry import load_benchmark_suites, load_evaluators

    domains = load_domains()
    if not domains:
        print("no domain packs installed")
        return 1
    if args.json:
        import json as _json

        from clousight_bench.core.inventory import inventory

        print(_json.dumps(inventory(), indent=2, ensure_ascii=False))
        return 0

    # Benchmark suites are the primary way to run something post-pivot: show
    # them FIRST, with a copy-pasteable task id.
    suites = load_benchmark_suites()
    if suites:
        print("benchmark suites:")
        for sid, suite in sorted(suites.items()):
            print(f"  suite:{sid:<18} {suite.suite_version}")
        evaluators = load_evaluators()
        if evaluators:
            names = ", ".join(
                f"{e.evaluator_id} ({'official' if e.official else 'custom'})" for e in evaluators
            )
            print(f"  evaluators : {names}")
        # Prefer the flagship swe-bench (always registered, domain known) as the
        # canonical copy-pasteable example — a suite is only runnable on its own
        # domain, so we must not pair an arbitrary first suite with a hardcoded
        # domain (e.g. suite:mmlu lives on `llm`, not `agent-runtime`).
        if "swe-bench" in suites:
            ex_suite, ex_domain = "swe-bench", "agent-runtime"
        else:
            ex_suite, ex_domain = sorted(suites)[0], "<domain>"
        print(
            f"  run one    : csbench run --domain {ex_domain} --task suite:{ex_suite}"
            " --platform local-sim --config <yaml with 'target: {mode: mock}'>"
        )
        print()

    for name, pack in sorted(domains.items()):
        print(f"domain: {name}")
        if pack.description:
            print(f"  {pack.description}")
        if not args.verbose:
            task_ids = sorted(pack.tasks())
            tasks_line = ", ".join(task_ids) if task_ids else "(none — runs arrive as suite:<id> jobs)"
            print(f"  tasks     : {tasks_line}")
            print(f"  platforms : {', '.join(sorted(pack.adapters()))}")
            continue
        print("  tasks:")
        for task_id, task_cls in sorted(pack.tasks().items()):
            tags = ", ".join(task_cls.capability_tags) or "—"
            print(f"    {task_id:<8} {task_cls.title}")
            print(f"             tags: {tags}")
        print("  platforms:")
        for platform, adapter_cls in sorted(pack.adapters().items()):
            provider = adapter_cls.provider or "local"
            print(f"    {platform:<24} status={adapter_cls.status} provider={provider}")
            if adapter_cls.target_example:
                import json as _json

                print(f"             target_example: {_json.dumps(adapter_cls.target_example)}")
    return 0


def _load_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    config_path = Path(path)
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UserInputError(f"config not found: {config_path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        # Anything else the filesystem/decoder can raise while reading a user-
        # supplied path (a directory, unreadable permissions, non-UTF-8 bytes,
        # ...) is a bad input, not an internal bug -- report it the same way.
        raise UserInputError(f"cannot read config {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise UserInputError(f"invalid YAML in {config_path}: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise UserInputError(f"config root must be a mapping: {config_path}")
    return data


def _check_target(target: dict[str, Any]) -> dict[str, Any]:
    """Reject the legacy ``oss_bucket`` target key with an actionable hint.

    ``oss_bucket`` was a cross-cloud smell (AWS users typed it to name an S3
    bucket); it is now ``blob_bucket``. Clean break, no compat alias -- fail
    loud so a stale config surfaces immediately instead of silently naming an
    empty bucket. Returns the same target unchanged when it is clean.
    """
    if "oss_bucket" in target:
        raise UserInputError(
            "target key 'oss_bucket' was renamed to 'blob_bucket'; update your target config"
        )
    return target


def _parse_params(pairs: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise UserInputError(f"--param expects key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        try:
            params[key] = json.loads(value)
        except json.JSONDecodeError:
            params[key] = value
    return params


_EXIT_BY_STATUS = {"completed": 0, "unsupported": 0, "failed": 1, "invalid": 1}


def _exit_code(record: Any) -> int:
    """Exit on the benchmark's verdict -- unless the record never reached disk.

    A run that measured perfectly but could not be written where the caller
    asked for it is not a success from a script's point of view: the file it
    is about to read is not there.
    """
    code = _EXIT_BY_STATUS[record.status]
    if record.run.stages.get("PERSIST") != "ok":
        code = max(code, 1)
    return code


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
        task_id=args.task,
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
        from clousight_bench.core.runplan import RunPlan, execute_plan

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


def _load_thresholds(path: str) -> dict[str, Any]:
    """Load a threshold map (YAML/JSON) for ``csbench run --assert``.

    Shape: ``{measurement_key: {min: x} | {max: y} | scalar(==min)}``.
    """
    raw = _load_config(path) if path else {}
    thresholds = raw.get("thresholds", raw) if isinstance(raw, dict) else {}
    if not isinstance(thresholds, dict) or not thresholds:
        raise UserInputError(f"--assert file {path!r} has no thresholds mapping")
    return thresholds


def _cmd_trace(args: argparse.Namespace) -> int:
    from clousight_bench.core.traceview import (
        find_trace,
        render_list,
        render_show,
        trace_summaries,
    )

    results = Path(args.results)
    if args.trace_cmd == "list":
        summaries = trace_summaries(results)
        if args.status:
            summaries = [s for s in summaries if s["status"] == args.status]
        print(render_list(summaries, sort=args.sort))
        return 0

    spans = find_trace(results, args.id)
    if spans is None:
        print(f"error: no trace for {args.id!r} under {results}/traces", file=sys.stderr)
        return 2
    print(render_show(spans))
    return 0


def _load_aggregates(results_dir: Path) -> list[dict]:
    import json

    from clousight_bench.core.runplan import AGGREGATES_DIRNAME

    agg_dir = results_dir / AGGREGATES_DIRNAME
    if not agg_dir.exists():
        return []
    best: dict[tuple[str, str, str], dict] = {}
    for path in sorted(agg_dir.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("kind") != "run_plan_aggregate":
            continue
        identity = data.get("identity", {})
        key = (identity.get("domain", ""), identity.get("task_id", ""), identity.get("adapter", ""))
        existing = best.get(key)
        this_n = data.get("plan", {}).get("repeat", 0)
        ex_n = existing.get("plan", {}).get("repeat", 0) if existing else -1
        if (
            existing is None
            or this_n > ex_n
            or (this_n == ex_n and data.get("plan_id", "") > existing.get("plan_id", ""))
        ):
            best[key] = data
    return list(best.values())


def _cmd_rollup(args: argparse.Namespace) -> int:
    from clousight_bench.core.rollup import rollup

    out = rollup(Path(args.run_dir), bucket_s=args.bucket_s)
    print(out)
    return 0


def _ensure_gitignore(root: Path, patterns: list[str]) -> None:
    gi = root / ".gitignore"
    existing = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    missing = [p for p in patterns if p not in existing]
    if missing:
        block = ["", "# clousight-bench private config (never commit secrets)"] + missing
        with gi.open("a", encoding="utf-8") as fh:
            fh.write(("\n" if existing and existing[-1] != "" else "") + "\n".join(block) + "\n")


def _cmd_init(args: argparse.Namespace) -> int:
    from clousight_bench.core.credentials import PROVIDER_CREDENTIALS

    provider = args.provider
    if provider not in PROVIDER_CREDENTIALS:
        raise UserInputError(f"unknown provider {provider!r}; choose from: {', '.join(PROVIDER_CREDENTIALS)}")
    spec = PROVIDER_CREDENTIALS[provider]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg_path = out_dir / f"{args.domain}-{provider}.local.yaml"
    profile_line = "  # profile: default        # optional: use a named CLI profile instead of env"
    cfg = (
        f"# {provider} · {args.domain} — private config. Safe to keep locally; DO NOT commit.\n"
        f"# Credentials are NOT stored here — they come from {provider}'s default chain\n"
        f"# (env vars / CLI profile / role). Run `csbench doctor --config {cfg_path.name}`.\n"
        "target:\n"
        f"  provider: {provider}\n"
        '  region: ""                 # fill in your region\n'
        f"{profile_line}\n"
        '  mock_base_url: ""          # public URL the cloud runtime can reach the mock at\n'
        "params: {}\n"
    )
    if cfg_path.exists() and not args.force:
        print(f"exists (use --force to overwrite): {cfg_path}")
    else:
        cfg_path.write_text(cfg, encoding="utf-8")
        print(f"wrote {cfg_path}")

    env_path = out_dir / ".env.example"
    env_lines = [
        f"# {provider} credentials for clousight-bench — copy to .env and fill in (never commit .env).",
        "# Alternatively use a CLI profile / role; then you don't need these at all.",
    ] + [f"{name}=" for name in spec["std_env"]]
    env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    print(f"wrote {env_path}")

    _ensure_gitignore(out_dir, ["*.local.yaml", ".env"])
    print(
        "\nnext:\n"
        f"  1. fill region + mock_base_url in {cfg_path.name}\n"
        f"  2. provide credentials (any one): export {' & '.join(spec['std_env'])}"
        f"  ·  or set target.profile  ·  or run the {provider} CLI login\n"
        f"  3. csbench doctor --config {cfg_path.name}\n"
        f"  docs: {spec['docs']}"
    )
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Preflight, standalone. Shares the exact check functions the orchestrator
    runs before a real run (single source of truth in core/preflight.py).

    With --domain/--platform (and optionally --task) it runs the *adapter's*
    full preflight, so you see the minimal permissions that specific benchmark
    needs on that cloud -- the (benchmark x cloud) matrix.

    A skeleton platform is never rejected here (that hard gate belongs to
    `csbench run`): doctor prints a warning that it is not implemented and
    still runs preflight, since the wiring / permission requirements it shows
    are exactly what a contributor needs before wiring the adapter."""
    from clousight_bench.core import preflight as pf
    from clousight_bench.core.credentials import PROVIDER_CREDENTIALS, infer_provider

    target: dict[str, Any] = {}
    cfg = _load_config(args.config)
    if cfg:
        target = _check_target(cfg.get("target", {}))
    if args.provider:
        target["provider"] = args.provider

    # Full adapter preflight when we can resolve a domain + platform.
    if args.domain and args.platform:
        from clousight_bench.core.registry import get_domain

        pack = get_domain(args.domain)
        adapter_classes = pack.adapters()
        if args.platform not in adapter_classes:
            raise UnknownPlatformError(
                f"platform {args.platform!r} not in domain {args.domain!r}: {sorted(adapter_classes)}"
            )
        adapter_cls = adapter_classes[args.platform]
        if not adapter_cls.is_runnable():
            # doctor is diagnostic, not execution: a skeleton must never look
            # runnable, but showing its wiring / preflight requirements (creds,
            # SDK, minimal permissions) is exactly what a contributor needs
            # before wiring it. `csbench run` keeps the hard skeleton gate.
            print(
                f"! {args.platform}: skeleton adapter — not implemented, "
                "cannot run. Showing wiring / preflight requirements only "
                "(not a live check of this platform)."
            )
        adapter = adapter_cls(target)
        task = None
        if args.task:
            task_classes = pack.tasks()
            if args.task not in task_classes:
                raise UnknownTaskError(
                    f"task {args.task!r} not in domain {args.domain!r}: {sorted(task_classes)}"
                )
            task = task_classes[args.task]()
        report = adapter.preflight(task)
        print(report.format())
        return 0 if report.ok else 1

    # Config/provider-only mode (no adapter): creds + sdk + mock.
    provider = infer_provider(target, args.platform)
    if provider is None:
        print(
            f"\u2717 provider — unknown; set target.provider or pass --provider "
            f"(one of: {', '.join(PROVIDER_CREDENTIALS)})"
        )
        return 1

    report = pf.PreflightReport()
    report.add(pf.Check("provider", ok=True, detail=provider))
    report.add(pf.credential_check(target, args.platform))
    report.add(pf.sdk_check(target, args.platform))
    report.add(pf.mock_reachable_check(str(target.get("mock_base_url", ""))))

    print(report.format())
    return 0 if report.ok else 1


def _cmd_query(args: argparse.Namespace) -> int:
    from clousight_bench.core.analytics import Analytics
    from clousight_bench.core.errors import UserInputError

    a = Analytics(Path(args.results))
    if args.table:
        where = f" WHERE {args.where}" if args.where else ""
        sql = f"SELECT * FROM {args.table}{where}"
    elif args.sql:
        sql = args.sql
    else:
        raise UserInputError("provide a SQL string or --table")
    try:
        rows = a.query(sql)
    except Exception as exc:  # noqa: BLE001 - a bad query is user input
        raise UserInputError(f"query failed: {exc}") from exc
    _print_rows(rows, args.format)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    from clousight_bench.core.analytics import Analytics
    from clousight_bench.core.errors import UserInputError

    try:
        out = Analytics(Path(args.results)).export(args.table, Path(args.out), fmt=args.format)
    except (ValueError, ImportError) as exc:
        raise UserInputError(str(exc)) from exc
    print(f"wrote {out}")
    return 0


def _print_rows(rows: list[dict], fmt: str) -> None:
    import json as _json

    if not rows:
        print("(no rows)")
        return
    if fmt == "json":
        print(_json.dumps(rows, ensure_ascii=False, default=str))
        return
    cols = list(rows[0].keys())
    if fmt == "csv":
        import csv
        import sys as _sys

        writer = csv.DictWriter(_sys.stdout, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    for row in rows:
        print("  ".join(str(row.get(c, "")).ljust(widths[c]) for c in cols))


def _cmd_sweep(args: argparse.Namespace) -> int:
    """Reconcile orphaned cloud resources a crashed run left behind.

    Reaps resources tagged with a clousight-bench run id (``core/resource_tags``)
    via the provider's installed ResourceReaper. Open-core installs none, so this
    fails clearly until a pack provides one. Dry-run by default: pass --confirm to
    actually delete."""
    from clousight_bench.core.registry import get_resource_reaper

    reaper = get_resource_reaper(args.provider)
    if reaper is None:
        print(
            f"no resource reaper installed for provider {args.provider!r}. "
            "Sweeping a real cloud needs the provider SDK + credentials, which a "
            "commercial pack supplies via the clousight_bench.resource_reapers "
            "entry point. Nothing was swept.",
            file=sys.stderr,
        )
        return 1

    dry_run = not args.confirm
    acted = reaper.sweep(dry_run=dry_run, older_than_s=args.older_than_s)
    verb = "would reap" if dry_run else "reaped"
    if not acted:
        print(f"{args.provider}: no orphaned resources found")
        return 0
    print(
        f"{args.provider}: {verb} {len(acted)} resource(s)"
        + ("  (dry-run; pass --confirm to delete)" if dry_run else "")
    )
    for res in acted:
        rid = res.get("id", "?")
        run = res.get("run_id", "?")
        print(f"  - {rid}  (run {run})")
    return 0


def _print_check_results(results: list, label: str) -> int:
    """Print CheckResult objects and return the number of failures."""
    failed = 0
    for r in results:
        mark = "✓" if r.ok else "✗"
        line = f"  {mark} {r.name}"
        if r.detail and not r.ok:
            line += f" -- {r.detail}"
        print(line)
        failed += 0 if r.ok else 1
    total = len(results)
    print(f"\nconformance: {total - failed}/{total} checks passed for {label!r}")
    return failed


def _cmd_conformance(args: argparse.Namespace) -> int:
    suite_id: str | None = getattr(args, "suite", None)
    domain: str | None = getattr(args, "domain", None)

    if suite_id is not None:
        return _cmd_conformance_suite(suite_id)

    # The mutually-exclusive required group guarantees one of the two is set,
    # but narrow explicitly (works under python -O, satisfies mypy).
    if domain is None:
        print("error: --domain or --suite is required", file=sys.stderr)
        return 2
    from clousight_bench.core.conformance import run_conformance

    results = run_conformance(domain, getattr(args, "platform", None))
    failed = _print_check_results(results, domain)
    return 0 if failed == 0 else 1


def _cmd_conformance_suite(suite_id: str) -> int:
    """Run conformance checks for every evaluator that supports ``suite_id``."""
    import shutil
    import tempfile

    from clousight_bench.core.conformance import CheckResult, check_evaluator
    from clousight_bench.core.registry import load_benchmark_suites, load_evaluators

    suites = load_benchmark_suites()
    if suite_id not in suites:
        available = ", ".join(sorted(suites)) or "<none installed>"
        print(
            f"error: suite {suite_id!r} not found. Available suites: {available}",
            file=sys.stderr,
        )
        return 2

    suite = suites[suite_id]
    tmp_dir = tempfile.mkdtemp(prefix="csbench-conformance-")
    known_suite_ids = sorted(suites)
    evaluators = load_evaluators()

    supporting = [ev for ev in evaluators if ev.supports(suite_id, "")]

    all_results: list[CheckResult] = []
    try:
        if not supporting:
            all_results.append(
                CheckResult(
                    "suite:has-evaluator",
                    False,
                    f"no evaluator registered for suite {suite_id!r}",
                )
            )
        else:
            raw = suite.mock_artifacts({"_tmp_dir": tmp_dir})
            for ev in supporting:
                ev_label = getattr(ev, "evaluator_id", str(ev))
                print(f"\n[evaluator: {ev_label}]")
                measurements = ev.evaluate(raw)
                results = check_evaluator(ev, suite_id, measurements, known_suite_ids=known_suite_ids)
                for r in results:
                    mark = "✓" if r.ok else "✗"
                    line = f"  {mark} {r.name}"
                    if r.detail and not r.ok:
                        line += f" -- {r.detail}"
                    print(line)
                all_results.extend(results)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    failed = sum(1 for r in all_results if not r.ok)
    total = len(all_results)
    print(f"\nconformance: {total - failed}/{total} checks passed for suite {suite_id!r}")
    return 0 if failed == 0 else 1


def _cmd_verify(args: argparse.Namespace) -> int:
    import json

    from clousight_bench.core.fingerprints import record_digest
    from clousight_bench.core.runplan import AGGREGATES_DIRNAME

    results_dir = Path(args.results)
    ok = failed = skipped = 0
    for path in sorted(results_dir.rglob("*.json")):
        rel = path.relative_to(results_dir)
        if AGGREGATES_DIRNAME in rel.parts:
            skipped += 1
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            stored = data.get("fingerprints", {}).get("record_digest", "")
            computed = record_digest(data)
            if stored == computed:
                print(f"✓  {rel}")
                ok += 1
            else:
                print(f"✗  {rel}")
                print(f"   stored:   {stored}")
                print(f"   computed: {computed}")
                failed += 1
        except Exception as exc:
            print(f"✗  {rel}  ({exc})")
            failed += 1
    total = ok + failed
    suffix = f" ({skipped} aggregate file(s) skipped)" if skipped else ""
    print(f"\nverified {total} file(s) — {ok} ok, {failed} failed{suffix}")
    return 0 if failed == 0 else 1


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

    from clousight_bench.core.campaign import (
        CampaignManifest,
        TaskProgress,
        new_campaign_id,
        write_manifest,
    )
    from clousight_bench.core.runplan import RunPlan, execute_plan
    from clousight_bench.core.schema import RunSpec

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


_STATE_ICON = {
    "completed": "✓",
    "failed": "✗",
    "running": "▶",
    "pending": "·",
}


def _fmt_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m{seconds % 60:02d}s"


def _render_progress(manifest: CampaignManifest) -> str:  # noqa: F821
    import time as _time

    from clousight_bench.core.campaign import TERMINAL_STATES

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

    from clousight_bench.core.campaign import (
        CampaignManifest,  # noqa: F401  (used by _render_progress annotation)
        load_manifest,
        manifest_path,
    )
    from clousight_bench.core.campaign import (
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

    from clousight_bench.core.campaign import TERMINAL_STATES

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


def _cmd_serve(args: argparse.Namespace) -> int:
    from clousight_bench.viewer.server import create_server, serve_until_interrupt

    # Bind first so the printed URL is the ACTUAL bound address (--port 0 picks
    # an ephemeral port; the placeholder 0 must never be shown to the user).
    server = create_server(Path(args.results), host=args.host, port=args.port)
    host_raw, bound_port = server.server_address[0], server.server_address[1]
    bound_host = host_raw.decode() if isinstance(host_raw, bytes) else host_raw  # AF_INET → str
    print(f"viewer: http://{bound_host}:{bound_port} (results: {args.results}) — Ctrl-C to stop")
    serve_until_interrupt(server)
    return 0


# ---- prod profile (ecs-resident orchestrator) helpers + commands ------------
_PROD_TF_DIR = "infra/terraform/aliyun-iam"


def _prod_target(config_path: str | None) -> dict:
    from pathlib import Path

    import yaml as _yaml

    doc = _yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) if config_path else {}
    return _check_target(dict((doc or {}).get("target") or {}))


def _prod_oss(target: dict):
    from clousight_bench.domains.agent_runtime.probe.oss_client import Oss2Client

    return Oss2Client(str(target.get("blob_bucket") or ""), str(target.get("region") or "cn-hangzhou"))


def _prod_channel(target: dict, campaign_id: str):
    from clousight_bench.core.campaign_channel import CampaignChannel

    return CampaignChannel(_prod_oss(target), campaign_id)


def _terraform_runner():
    import subprocess

    return lambda argv: subprocess.call(["terraform", *argv], cwd=_PROD_TF_DIR)


def _prod_runtime_deleter(target: dict):
    def _del(runtime_id: str) -> None:
        from clousight_bench.domains.agent_runtime.adapters.cn_clouds import AliyunAgentRunAdapter

        AliyunAgentRunAdapter(target).transport().deprovision(runtime_id)

    return _del


def _controller_extra_deps(needs_swebench: bool) -> list[str]:
    """Controller pip deps: the full orchestrator needs probe + aliyun SDKs + store.

    ``needs_swebench=True`` (any ``suite:`` task in the plan) additionally pulls
    the ``[swebench]`` harness extra so the driver host can actually evaluate —
    without it the suite fails at run() with "swebench extra not installed".
    """
    from clousight_bench.domains.agent_runtime.dev_wheel import deps_for_extras

    extras = ["probe", "aliyun", "store"] + (["swebench"] if needs_swebench else [])
    fallback = ["requests>=2.28", "oss2>=2.18", "duckdb>=1.0", "pyarrow>=16"]
    if needs_swebench:
        fallback.append("swebench>=3.0")
    return deps_for_extras(extras) or fallback


def _prod_wheel_builder(target: dict):
    """Build+upload the private dev wheel; return (campaign_id, needs_swebench) -> (url, deps)."""

    def _build(campaign_id: str, needs_swebench: bool = False) -> tuple[str, list[str]]:
        from clousight_bench.domains.agent_runtime.dev_wheel import upload_dev_wheel
        from clousight_bench.domains.agent_runtime.probe.oss_client import Oss2Client

        bucket = str(target.get("blob_bucket") or "")
        region = str(target.get("region") or "cn-hangzhou")
        upload = Oss2Client(bucket, region)  # public endpoint for the PUT
        sign = Oss2Client(bucket, region, internal=True)  # internal endpoint for the presign
        url = upload_dev_wheel(upload, sign, campaign_id, expires=7200)
        return url, _controller_extra_deps(needs_swebench)

    return _build


def _cmd_submit(args: argparse.Namespace) -> int:
    from clousight_bench.core import prod_submit
    from clousight_bench.core.campaign_channel import CampaignChannel

    target = _prod_target(args.config)
    oss = _prod_oss(target)
    cid = prod_submit.submit(
        args.plan_file,
        args.config,
        lambda c: CampaignChannel(oss, c),
        _terraform_runner(),
        watchdog_timeout_s=args.watchdog_timeout,
        wheel_builder=_prod_wheel_builder(target),
    )
    print(cid)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    import json as _json

    from clousight_bench.core import prod_submit

    st = prod_submit.status(_prod_channel(_prod_target(args.config), args.campaign_id))
    print(_json.dumps(st, ensure_ascii=False))
    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    from clousight_bench.core import prod_submit

    for line in prod_submit.logs(_prod_channel(_prod_target(args.config), args.campaign_id)):
        print(line)
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    from clousight_bench.core import prod_submit

    paths = prod_submit.fetch(_prod_channel(_prod_target(args.config), args.campaign_id), args.dest)
    print(f"fetched {len(paths)} file(s) to {args.dest}")
    return 0


def _cmd_teardown(args: argparse.Namespace) -> int:
    from clousight_bench.core import prod_submit
    from clousight_bench.core.credentials import infer_provider

    target = _prod_target(args.config)
    out = prod_submit.teardown(
        _prod_channel(target, args.campaign_id),
        _terraform_runner(),
        _prod_runtime_deleter(target),
        # resolves the provider's controller terraform surface (ControllerTfSpec)
        provider=infer_provider(target),
    )
    print(out)
    return 0


def _dispatch(args: argparse.Namespace) -> int:
    handlers = {
        "list": _cmd_list,
        "submit": _cmd_submit,
        "status": _cmd_status,
        "logs": _cmd_logs,
        "fetch": _cmd_fetch,
        "teardown": _cmd_teardown,
        "run": _cmd_run,
        "trace": _cmd_trace,
        "rollup": _cmd_rollup,
        "init": _cmd_init,
        "doctor": _cmd_doctor,
        "sweep": _cmd_sweep,
        "conformance": _cmd_conformance,
        "query": _cmd_query,
        "export": _cmd_export,
        "verify": _cmd_verify,
        "run-plan": _cmd_run_plan,
        "progress": _cmd_progress,
        "serve": _cmd_serve,
    }
    return handlers[args.command](args)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        prog="csbench", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="list installed domains, tasks and platforms")
    list_p.add_argument("--verbose", action="store_true", help="show task and adapter metadata")
    list_p.add_argument("--json", action="store_true", help="output as JSON (LLM-friendly)")

    run_p = sub.add_parser("run", help="run one task against one platform")
    run_p.add_argument("--domain", required=True)
    run_p.add_argument("--task", required=True)
    run_p.add_argument("--platform", required=True)
    run_p.add_argument("--config", help="YAML file with `target:` and `params:` sections")
    run_p.add_argument("--param", action="append", default=[], help="override a task param, key=value")
    run_p.add_argument("--results", default=str(DEFAULT_RESULTS_DIR))
    run_p.add_argument("--no-enrich", action="store_true", help="skip result enrichers")
    run_p.add_argument(
        "--assert",
        dest="assert_thresholds",
        metavar="THRESHOLDS.yaml",
        help="CI gate: a YAML/JSON threshold map ({key: {min: x} | {max: y}}); "
        "exit non-zero if any measurement misses its bound",
    )
    run_p.add_argument(
        "--skip-preflight",
        action="store_true",
        help="skip the preflight prerequisite checks (not recommended)",
    )
    run_p.add_argument(
        "--debug",
        action="store_true",
        help="write stage tracebacks to <results>/debug/<run_id>.log (never into the record)",
    )
    run_p.add_argument(
        "--repeat", type=int, default=1, help="measured repeats to run and aggregate (default: 1)"
    )
    run_p.add_argument(
        "--warmup", type=int, default=0, help="warmup runs to execute first and exclude from statistics"
    )
    run_p.add_argument("--plan-id", help="reuse a plan id (printed in the aggregate) to resume it")
    run_p.add_argument(
        "--resume",
        action="store_true",
        help="skip repeats already completed under --plan-id; re-run interrupted/missing ones",
    )
    run_p.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="per-run deadline in seconds for setup+execute+collect "
        "(guards against a hung stage; teardown still runs)",
    )
    run_p.add_argument(
        "--allow-live",
        action="store_true",
        help="acknowledge real-cloud cost: required to run a live "
        "(non-simulated) platform. Simulated runs never need it. "
        "Env: CSBENCH_ALLOW_LIVE=1",
    )
    run_p.add_argument(
        "--cost-budget",
        type=float,
        default=None,
        help="cumulative USD cap across runs in this --results dir; a "
        "billable run that would cross it is stopped before "
        "provisioning. Env: CSBENCH_COST_BUDGET",
    )

    rp_p = sub.add_parser("run-plan", help="run a YAML batch plan (multiple tasks with repeat/warmup)")
    rp_p.add_argument("plan_file", help="YAML plan file (see examples/run-plan-example.yaml)")
    rp_p.add_argument("--config", help="YAML file with base `target:` and `params:` (merged with plan file)")
    rp_p.add_argument("--results", default=str(DEFAULT_RESULTS_DIR))
    rp_p.add_argument(
        "--allow-live", action="store_true", help="acknowledge real-cloud cost (required for live platforms)"
    )
    rp_p.add_argument(
        "--cost-budget", type=float, default=None, help="cumulative USD cap across all tasks in this plan"
    )
    rp_p.add_argument(
        "--probe",
        choices=["local", "ecs", "eci"],
        default="local",
        help="probe mode: 'local' keeps in-process behavior (default); "
        "'ecs' brings up a per-campaign in-region ECS probe carrier "
        "('eci' is a deprecated alias for 'ecs')",
    )
    rp_p.add_argument(
        "--mode",
        choices=["dev", "prod"],
        default="dev",
        help="run profile by intent: 'dev' (local development) runs here; 'prod' (production benchmarking) "
        "is rejected — use `csbench submit` (the ecs-resident orchestrator)",
    )

    # ---- prod profile: thin submit/status/logs/fetch/teardown -----------------
    sm_p = sub.add_parser("submit", help="prod: submit a campaign to an ecs-resident controller")
    sm_p.add_argument("plan_file", help="YAML plan file")
    sm_p.add_argument("--config", required=True, help="YAML with target: (needs blob_bucket + region)")
    sm_p.add_argument(
        "--watchdog-timeout",
        type=float,
        default=5400.0,
        dest="watchdog_timeout",
        help="controller self-destruct timeout in seconds (default 5400 = 90min)",
    )
    for _name, _help in (
        ("status", "prod: show a campaign's progress"),
        ("logs", "prod: show the controller's logs"),
        ("teardown", "prod: backstop cleanup (stop + reap residual + terraform destroy)"),
    ):
        _p = sub.add_parser(_name, help=_help)
        _p.add_argument("campaign_id")
        _p.add_argument("--config", required=True, help="YAML with target: (needs blob_bucket + region)")
    fe_p = sub.add_parser("fetch", help="prod: download a campaign's results (JSON + parquet)")
    fe_p.add_argument("campaign_id")
    fe_p.add_argument("--config", required=True, help="YAML with target: (needs blob_bucket + region)")
    fe_p.add_argument("--dest", default="results/prod-fetch", help="destination directory")

    prog_p = sub.add_parser("progress", help="show a run-plan campaign's live progress")
    prog_p.add_argument("--results", default=str(DEFAULT_RESULTS_DIR))
    prog_p.add_argument("--campaign", help="campaign id (default: the most recently updated one)")
    prog_p.add_argument("--json", action="store_true", help="print the raw manifest as JSON")
    prog_p.add_argument(
        "--watch", action="store_true", help="redraw until every task reaches a terminal state"
    )
    prog_p.add_argument(
        "--interval", type=float, default=2.0, help="seconds between redraws when --watch (default: 2)"
    )

    serve_p = sub.add_parser("serve", help="local read-only web viewer over a results directory")
    serve_p.add_argument(
        "--results", default=str(DEFAULT_RESULTS_DIR), help="results directory (default: results)"
    )
    serve_p.add_argument("--port", type=int, default=8787, help="port to listen on (default: 8787)")
    serve_p.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")

    trace_p = sub.add_parser("trace", help="inspect the execution traces of runs")
    trace_sub = trace_p.add_subparsers(dest="trace_cmd", required=True)
    tl = trace_sub.add_parser("list", help="list traces (one row per run)")
    tl.add_argument("--results", default=str(DEFAULT_RESULTS_DIR))
    tl.add_argument(
        "--sort",
        choices=["started", "duration"],
        default="started",
        help="order by start (default) or total duration",
    )
    tl.add_argument("--status", help="only traces with this run status")
    ts = trace_sub.add_parser("show", help="render one run's trace as a stage tree")
    ts.add_argument("id", help="a run_id or trace_id")
    ts.add_argument("--results", default=str(DEFAULT_RESULTS_DIR))

    roll_p = sub.add_parser("rollup", help="downsample a run's series.parquet (needs the [store] extra)")
    roll_p.add_argument("run_dir", help="directory containing series.parquet")
    roll_p.add_argument("--bucket-s", type=int, default=1, help="bucket width in seconds (default: 1)")

    init_p = sub.add_parser("init", help="scaffold a private config + .env.example for a provider")
    init_p.add_argument("provider", help="cloud provider (aws, aliyun, huawei, volcengine)")
    init_p.add_argument("--domain", default="agent-runtime")
    init_p.add_argument("--out", default=".", help="output directory (default: current dir)")
    init_p.add_argument("--force", action="store_true", help="overwrite existing config")

    doc_p = sub.add_parser("doctor", help="preflight: check credentials + connectivity before a run")
    doc_p.add_argument("--config", help="YAML config to check")
    doc_p.add_argument("--provider", help="provider override (aws/aliyun/huawei/volcengine)")
    doc_p.add_argument("--platform", help="platform name, used to infer provider")
    doc_p.add_argument("--domain", help="domain; with --platform runs the adapter's full preflight")
    doc_p.add_argument("--task", help="task id; check the minimal permissions THIS benchmark needs")

    sweep_p = sub.add_parser(
        "sweep", help="reap orphaned cloud resources a crashed run left behind (needs a reaper plugin)"
    )
    sweep_p.add_argument("--provider", required=True, help="cloud provider (aws/aliyun/huawei/volcengine)")
    sweep_p.add_argument(
        "--confirm", action="store_true", help="actually delete (default: dry-run, only report)"
    )
    sweep_p.add_argument(
        "--older-than-s",
        type=float,
        default=None,
        help="only resources older than this many seconds (protects in-flight runs)",
    )

    conf_p = sub.add_parser("conformance", help="check an installed domain plugin or suite evaluator")
    _conf_mode = conf_p.add_mutually_exclusive_group(required=True)
    _conf_mode.add_argument("--domain", default=None, help="domain plugin to check")
    _conf_mode.add_argument(
        "--suite", default=None, help="suite id; checks every registered evaluator for it"
    )
    conf_p.add_argument(
        "--platform", default=None, help="(domain mode only) assert this platform's adapter is declared"
    )

    ver_p = sub.add_parser("verify", help="verify record_digest integrity for all result files")
    ver_p.add_argument(
        "--results", default=str(DEFAULT_RESULTS_DIR), help="results directory to scan (default: ./results)"
    )

    q_p = sub.add_parser("query", help="SQL over records/measurements/findings/series (needs [store])")
    q_p.add_argument("sql", nargs="?", help="a SQL query over the four views")
    q_p.add_argument(
        "--table",
        choices=["records", "measurements", "findings", "series"],
        help="shortcut for SELECT * FROM <table>",
    )
    q_p.add_argument("--where", help="WHERE clause used with --table")
    q_p.add_argument("--results", default=str(DEFAULT_RESULTS_DIR))
    q_p.add_argument("--format", choices=["table", "csv", "json"], default="table")

    x_p = sub.add_parser("export", help="export a long table to parquet/csv/jsonl")
    x_p.add_argument("table", choices=["records", "measurements", "findings", "series"])
    x_p.add_argument("--out", required=True)
    x_p.add_argument("--format", choices=["parquet", "csv", "jsonl"], default="parquet")
    x_p.add_argument("--results", default=str(DEFAULT_RESULTS_DIR))

    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except UserInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("hint: run `csbench list --verbose` to inspect valid choices", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        # The orchestrator already ran teardown and persisted an interrupted
        # record; report cleanly instead of dumping a traceback.
        print("\ninterrupted: teardown ran and progress was saved", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
