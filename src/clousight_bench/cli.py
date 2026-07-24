"""csbench: the Clousight Bench command line.

    csbench list                                        # installed domains / tasks / platforms
    csbench run --domain agent-runtime --task T1.3 \
            --platform local-sim [--config cfg.yaml] [--param k=v ...]
    csbench report [--results results/] [--out results/comparison.md]
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
    record = execute(spec, results_dir=Path(args.results), enrich=not args.no_enrich)
    print(record.to_json())
    return 0 if record.ok else 2


def _cmd_report(args: argparse.Namespace) -> int:
    from clousight_bench.core.report import generate_report

    out = generate_report(Path(args.results), Path(args.out) if args.out else None)
    print(out)
    return 0


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
        raise SystemExit(f"unknown provider {provider!r}; choose from: {', '.join(PROVIDER_CREDENTIALS)}")
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
    import importlib.util
    from urllib import request

    from clousight_bench.core.credentials import (
        PROVIDER_CREDENTIALS,
        infer_provider,
        resolve_credentials,
    )

    target: dict[str, Any] = {}
    if args.config:
        cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
        target = cfg.get("target", {})
    if args.provider:
        target["provider"] = args.provider

    ok_mark, warn_mark, bad_mark = "✓", "!", "✗"
    lines: list[str] = []
    critical_ok = True

    provider = infer_provider(target, args.platform)
    if provider is None:
        print(f"{bad_mark} provider — unknown; set target.provider or pass --provider "
              f"(one of: {', '.join(PROVIDER_CREDENTIALS)})")
        return 1
    lines.append(f"{ok_mark} provider — {provider}")

    # SDK installed? (warning only — resolution/doctor don't import it)
    sdk = PROVIDER_CREDENTIALS[provider]["sdk_module"]
    if importlib.util.find_spec(sdk) is not None:
        lines.append(f"{ok_mark} sdk — {sdk} importable")
    else:
        lines.append(f"{warn_mark} sdk — {sdk} not installed (needed only to run real "
                     f"tasks; `pip install {sdk}`)")

    # Credentials (critical)
    res = resolve_credentials(target, platform=args.platform)
    if res.ok:
        lines.append(f"{ok_mark} credentials — via {res.source} ({res.identity_hint})")
    else:
        critical_ok = False
        lines.append(f"{bad_mark} credentials — {res.remediation}")

    # mock_base_url reachability (warning; local-sim doesn't need it)
    mock = str(target.get("mock_base_url", "")).strip()
    if not mock:
        lines.append(f"{warn_mark} mock_base_url — not set (required before a real cloud run)")
    elif mock.startswith(("http://127.", "http://localhost", "https://localhost")):
        lines.append(f"{bad_mark} mock_base_url — localhost is NOT reachable from a cloud "
                     f"runtime; expose via a tunnel or cloud function")
        critical_ok = False
    else:
        try:
            with request.urlopen(f"{mock.rstrip('/')}/health", timeout=5) as resp:
                reachable = 200 <= resp.status < 300
            mark = ok_mark if reachable else warn_mark
            lines.append(f"{mark} mock_base_url — {mock} (/health -> {'ok' if reachable else 'non-2xx'})")
        except Exception as exc:  # noqa: BLE001 - report, don't crash the doctor
            lines.append(f"{warn_mark} mock_base_url — {mock} unreachable now ({type(exc).__name__})")

    print("\n".join(lines))
    return 0 if critical_ok else 1


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
    run_p.add_argument("--no-enrich", action="store_true", help="skip result enrichers")

    rep_p = sub.add_parser("report", help="aggregate results into a comparison report")
    rep_p.add_argument("--results", default=str(DEFAULT_RESULTS_DIR))
    rep_p.add_argument("--out", help="write markdown here (default: <results>/comparison.md)")

    init_p = sub.add_parser("init", help="scaffold a private config + .env.example for a provider")
    init_p.add_argument("provider", help="cloud provider (aws, aliyun, huawei, volcengine)")
    init_p.add_argument("--domain", default="agent-runtime")
    init_p.add_argument("--out", default=".", help="output directory (default: current dir)")
    init_p.add_argument("--force", action="store_true", help="overwrite existing config")

    doc_p = sub.add_parser("doctor", help="preflight: check credentials + connectivity before a run")
    doc_p.add_argument("--config", help="YAML config to check")
    doc_p.add_argument("--provider", help="provider override (aws/aliyun/huawei/volcengine)")
    doc_p.add_argument("--platform", help="platform name, used to infer provider")

    args = parser.parse_args(argv)
    if args.command == "list":
        return _cmd_list(args)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "report":
        return _cmd_report(args)
    if args.command == "init":
        return _cmd_init(args)
    if args.command == "doctor":
        return _cmd_doctor(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
