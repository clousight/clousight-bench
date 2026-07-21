"""csbench: the Clousight Bench command line.

    csbench list                                        # installed domains / tasks / platforms
    csbench run --domain agent-runtime --task T1.3 \
            --platform local-sim [--config cfg.yaml] [--param k=v ...]
    csbench report [--results results/] [--out results/comparison.md]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

from clousight_bench.core.orchestrator import DEFAULT_RESULTS_DIR, execute
from clousight_bench.core.registry import load_domains
from clousight_bench.core.schema import RunSpec


def _cmd_list(_args: argparse.Namespace) -> int:
    domains = load_domains()
    if not domains:
        print("no domain packs installed")
        return 1
    for name, pack in sorted(domains.items()):
        print(f"domain: {name}")
        if pack.description:
            print(f"  {pack.description}")
        print(f"  tasks     : {', '.join(sorted(pack.tasks()))}")
        print(f"  platforms : {', '.join(sorted(pack.adapters()))}")
    return 0


def _parse_params(pairs: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--param expects key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        try:
            params[key] = json.loads(value)
        except json.JSONDecodeError:
            params[key] = value
    return params


def _cmd_run(args: argparse.Namespace) -> int:
    target: dict[str, Any] = {}
    params: dict[str, Any] = {}
    if args.config:
        cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
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
    record = execute(spec, results_dir=Path(args.results))
    print(record.to_json())
    return 0 if record.ok else 2


def _cmd_report(args: argparse.Namespace) -> int:
    from clousight_bench.core.report import generate_report

    out = generate_report(Path(args.results), Path(args.out) if args.out else None)
    print(out)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="csbench", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list installed domains, tasks and platforms")

    run_p = sub.add_parser("run", help="run one task against one platform")
    run_p.add_argument("--domain", required=True)
    run_p.add_argument("--task", required=True)
    run_p.add_argument("--platform", required=True)
    run_p.add_argument("--config", help="YAML file with `target:` and `params:` sections")
    run_p.add_argument("--param", action="append", default=[], help="override a task param, key=value")
    run_p.add_argument("--results", default=str(DEFAULT_RESULTS_DIR))

    rep_p = sub.add_parser("report", help="aggregate results into a comparison report")
    rep_p.add_argument("--results", default=str(DEFAULT_RESULTS_DIR))
    rep_p.add_argument("--out", help="write markdown here (default: <results>/comparison.md)")

    args = parser.parse_args(argv)
    if args.command == "list":
        return _cmd_list(args)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "report":
        return _cmd_report(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
