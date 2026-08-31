"""Result-reading commands: query, export, verify, rollup, trace, serve."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

import argparse
import json
import sys
from pathlib import Path

from clousight_bench.core.errors import (
    UserInputError,
)


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


def _cmd_rollup(args: argparse.Namespace) -> int:
    from clousight_bench.core.rollup import rollup

    out = rollup(Path(args.run_dir), bucket_s=args.bucket_s)
    print(out)
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    from clousight_bench.core.analytics import Analytics

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


def _cmd_verify(args: argparse.Namespace) -> int:

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
