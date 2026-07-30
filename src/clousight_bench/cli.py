"""csbench: the Clousight Bench command line.

    csbench list                                        # installed domains / tasks / platforms
    csbench run --domain agent-runtime --task T1.3 \
            --platform local-sim [--config cfg.yaml] [--param k=v ...] [--debug]
    #   --repeat N --warmup W  -> run a plan and print a statistical aggregate
    csbench report [--results results/] [--out results/comparison.md]
    #   results/publish-receipts.jsonl records publish attempts (append-only)
    csbench migrate-results old-results/ --output new-results/ [--dry-run]
    csbench init aws [--domain agent-runtime] [--out .]  # scaffold private config + .env.example
    csbench doctor --config x.local.yaml                 # preflight: creds + connectivity
"""
from __future__ import annotations

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
    domains = load_domains()
    if not domains:
        print("no domain packs installed")
        return 1
    for name, pack in sorted(domains.items()):
        print(f"domain: {name}")
        if pack.description:
            print(f"  {pack.description}")
        if not args.verbose:
            print(f"  tasks     : {', '.join(sorted(pack.tasks()))}")
            print(f"  platforms : {', '.join(sorted(pack.adapters()))}")
            continue
        print("  tasks:")
        for task_id, task_cls in sorted(pack.tasks().items()):
            print(
                f"    {task_id:<8} {task_cls.title} "
                f"[evidence={task_cls.evidence_layer}]"
            )
        print("  platforms:")
        for platform, adapter_cls in sorted(pack.adapters().items()):
            provider = adapter_cls.provider or "local"
            print(
                f"    {platform:<24} status={adapter_cls.status} "
                f"provider={provider}"
            )
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
        target = cfg.get("target", {})
        params = cfg.get("params", {})
    params.update(_parse_params(args.param))

    spec = RunSpec(
        domain=args.domain,
        task_id=args.task,
        platform=args.platform,
        target=target,
        params=params,
    )

    if args.repeat != 1 or args.warmup != 0 or args.plan_id or args.resume:
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
    )
    print(record.to_json())
    return _exit_code(record)


def _cmd_report(args: argparse.Namespace) -> int:
    from clousight_bench.core.report import generate_report

    out = generate_report(Path(args.results), Path(args.out) if args.out else None)
    print(out)
    return 0


def _cmd_rollup(args: argparse.Namespace) -> int:
    from clousight_bench.core.rollup import rollup

    out = rollup(Path(args.run_dir), bucket_s=args.bucket_s)
    print(out)
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    from clousight_bench.core.migrate import MANIFEST_FILE, migrate_tree

    dest = Path(args.output)
    manifest = migrate_tree(Path(args.source), dest, dry_run=args.dry_run)
    prefix = "dry-run: " if args.dry_run else ""
    print(
        f"{prefix}migrated={manifest.migrated} "
        f"skipped={manifest.skipped} failed={manifest.failed}"
    )
    for entry in manifest.entries:
        if entry.status != "migrated":
            print(f"  {entry.status}: {entry.source} — {entry.reason}")
    if not args.dry_run:
        print(f"manifest: {dest.resolve() / MANIFEST_FILE}")
    return 1 if manifest.failed else 0


def _ensure_gitignore(root: Path, patterns: list[str]) -> None:
    gi = root / ".gitignore"
    existing = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    missing = [p for p in patterns if p not in existing]
    if missing:
        block = (["", "# clousight-bench private config (never commit secrets)"] + missing)
        with gi.open("a", encoding="utf-8") as fh:
            fh.write(("\n" if existing and existing[-1] != "" else "") + "\n".join(block) + "\n")


def _cmd_init(args: argparse.Namespace) -> int:
    from clousight_bench.core.credentials import PROVIDER_CREDENTIALS

    provider = args.provider
    if provider not in PROVIDER_CREDENTIALS:
        raise UserInputError(
            f"unknown provider {provider!r}; "
            f"choose from: {', '.join(PROVIDER_CREDENTIALS)}"
        )
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
        "  region: \"\"                 # fill in your region\n"
        f"{profile_line}\n"
        "  mock_base_url: \"\"          # public URL the cloud runtime can reach the mock at\n"
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
        target = cfg.get("target", {})
    if args.provider:
        target["provider"] = args.provider

    # Full adapter preflight when we can resolve a domain + platform.
    if args.domain and args.platform:
        from clousight_bench.core.registry import get_domain

        pack = get_domain(args.domain)
        adapter_classes = pack.adapters()
        if args.platform not in adapter_classes:
            raise UnknownPlatformError(
                f"platform {args.platform!r} not in domain {args.domain!r}: "
                f"{sorted(adapter_classes)}"
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
                    f"task {args.task!r} not in domain {args.domain!r}: "
                    f"{sorted(task_classes)}"
                )
            task = task_classes[args.task]()
        report = adapter.preflight(task)
        print(report.format())
        return 0 if report.ok else 1

    # Config/provider-only mode (no adapter): creds + sdk + mock.
    provider = infer_provider(target, args.platform)
    if provider is None:
        print(f"\u2717 provider — unknown; set target.provider or pass --provider "
              f"(one of: {', '.join(PROVIDER_CREDENTIALS)})")
        return 1

    report = pf.PreflightReport()
    report.add(pf.Check("provider", ok=True, detail=provider))
    report.add(pf.credential_check(target, args.platform))
    report.add(pf.sdk_check(target, args.platform))
    report.add(pf.mock_reachable_check(str(target.get("mock_base_url", ""))))

    print(report.format())
    return 0 if report.ok else 1


def _dispatch(args: argparse.Namespace) -> int:
    handlers = {
        "list": _cmd_list,
        "run": _cmd_run,
        "report": _cmd_report,
        "rollup": _cmd_rollup,
        "migrate-results": _cmd_migrate,
        "init": _cmd_init,
        "doctor": _cmd_doctor,
    }
    return handlers[args.command](args)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="csbench", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="list installed domains, tasks and platforms")
    list_p.add_argument("--verbose", action="store_true", help="show task and adapter metadata")

    run_p = sub.add_parser("run", help="run one task against one platform")
    run_p.add_argument("--domain", required=True)
    run_p.add_argument("--task", required=True)
    run_p.add_argument("--platform", required=True)
    run_p.add_argument("--config", help="YAML file with `target:` and `params:` sections")
    run_p.add_argument("--param", action="append", default=[], help="override a task param, key=value")
    run_p.add_argument("--results", default=str(DEFAULT_RESULTS_DIR))
    run_p.add_argument("--no-enrich", action="store_true", help="skip result enrichers")
    run_p.add_argument("--skip-preflight", action="store_true",
                       help="skip the preflight prerequisite checks (not recommended)")
    run_p.add_argument("--debug", action="store_true",
                       help="write stage tracebacks to <results>/debug/<run_id>.log "
                            "(never into the record)")
    run_p.add_argument("--repeat", type=int, default=1,
                       help="measured repeats to run and aggregate (default: 1)")
    run_p.add_argument("--warmup", type=int, default=0,
                       help="warmup runs to execute first and exclude from statistics")
    run_p.add_argument("--plan-id", help="reuse a plan id (printed in the aggregate) to resume it")
    run_p.add_argument("--resume", action="store_true",
                       help="skip repeats already completed under --plan-id; re-run interrupted/missing ones")

    rep_p = sub.add_parser("report", help="aggregate results into a comparison report")
    rep_p.add_argument("--results", default=str(DEFAULT_RESULTS_DIR))
    rep_p.add_argument("--out", help="write markdown here (default: <results>/comparison.md)")

    roll_p = sub.add_parser("rollup", help="downsample a run's series.parquet (needs the [store] extra)")
    roll_p.add_argument("run_dir", help="directory containing series.parquet")
    roll_p.add_argument("--bucket-s", type=int, default=1, help="bucket width in seconds (default: 1)")

    mig_p = sub.add_parser(
        "migrate-results",
        help="convert schema 1.0 result files into schema 0.2 (never in place)",
    )
    mig_p.add_argument("source", help="directory containing schema 1.0 result JSON")
    mig_p.add_argument(
        "--output",
        required=True,
        help="fresh destination directory; must be outside SOURCE",
    )
    mig_p.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be migrated without writing anything",
    )

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
