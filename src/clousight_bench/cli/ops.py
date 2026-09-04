"""Operational commands: list, init, doctor, sweep, conformance."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

import argparse
import sys
from pathlib import Path
from typing import Any

from clousight_bench.cli._common import _check_target, _load_config
from clousight_bench.core.errors import (
    UnknownPlatformError,
    UnknownTaskError,
    UserInputError,
)
from clousight_bench.core.registry import load_domains


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
            f"  run one    : csbench run --domain {ex_domain} --benchmark {ex_suite}"
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
            if args.task.startswith("suite:"):
                # Benchmarks are the public unit: resolve the suite and hand
                # preflight its declared permission tokens.
                from types import SimpleNamespace

                from clousight_bench.core.registry import load_benchmark_suites

                suite_id = args.task.removeprefix("suite:")
                suites = load_benchmark_suites()
                if suite_id not in suites:
                    raise UnknownTaskError(
                        f"suite {suite_id!r} is not a registered benchmark suite: {sorted(suites)}"
                    )
                suite = suites[suite_id]
                task = SimpleNamespace(
                    task_id=args.task,
                    required_permissions=tuple(getattr(suite, "required_permissions", ()) or ()),
                )
            else:
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
    from clousight_bench.core.suite import evaluate_with_metrics

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
                # Include bound add-on metric outputs so the namespace/official
                # checks cover them too (they emit <suite_id>.<metric_id>).
                measurements, _ = evaluate_with_metrics(ev, raw, suite_id=suite_id)
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
