"""The ``csbench`` argument parser + command dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

import argparse
import logging
import sys

from clousight_bench.cli.ops import (
    _cmd_conformance,
    _cmd_doctor,
    _cmd_init,
    _cmd_list,
    _cmd_sweep,
)
from clousight_bench.cli.prod import (
    _cmd_fetch,
    _cmd_logs,
    _cmd_status,
    _cmd_submit,
    _cmd_teardown,
)
from clousight_bench.cli.results import (
    _cmd_export,
    _cmd_query,
    _cmd_rollup,
    _cmd_serve,
    _cmd_trace,
    _cmd_verify,
)
from clousight_bench.cli.run import _cmd_progress, _cmd_run, _cmd_run_plan
from clousight_bench.core.errors import (
    UserInputError,
)
from clousight_bench.core.orchestrator import DEFAULT_RESULTS_DIR


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

    run_p = sub.add_parser("run", help="run one benchmark against one platform")
    run_p.add_argument("--domain", required=True)
    run_p.add_argument(
        "--benchmark",
        help="a registered benchmark suite id (e.g. swe-bench) — the standard way to run",
    )
    run_p.add_argument(
        "--task",
        help="a native/internal task id, or the explicit canonical form suite:<id> "
        "(--benchmark <id> is the preferred sugar)",
    )
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
    ti = trace_sub.add_parser(
        "import", help="convert external OTel spans (OTLP JSON or flat JSONL) into a v3 trajectory"
    )
    ti.add_argument("file", help="OTLP/JSON export or span-per-line JSONL")
    ti.add_argument("--out", help="output path (default: <file>.v3.jsonl)")
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
