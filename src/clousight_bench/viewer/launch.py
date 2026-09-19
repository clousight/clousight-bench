"""Starting a run from the browser.

This is a process launcher reachable from a web page, so the interesting part
is everything it refuses. Two rules shape the whole module:

**No command, no path.** The request body is a fixed set of fields. There is
no entry for "a command line" and none for "a file"; ``target`` is the *name*
of a config, resolved inside ``configs/`` by viewer/targets.py, and the argv is
built from the validated fields one flag at a time. Nothing a caller sends is
ever concatenated into a shell string — there is no shell.

**Validate against what is installed, now.** Domain, platform and benchmark are
checked against the live plugin registry — the same registry the run itself
will resolve against — and so is whether any evaluator can score this benchmark
on this platform. Checking that here turns a failure at SCORE, minutes later
and buried in a subprocess log, into a sentence under the form.

The run is a detached subprocess. A TPC-H run takes minutes; doing it on the
HTTP thread would hang the server that is supposed to be showing the progress.
The id is generated *here* and passed in with ``--run-id``, so the reply can
name a run the live view can open immediately.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from clousight_bench.core.logsafe import sanitize_for_log
from clousight_bench.viewer.targets import target_path, valid_target_name

logger = logging.getLogger(__name__)

#: Hard ceilings, deliberately small. `repeat` is what turns one click into an
#: afternoon of billable work, so the bound is written here rather than left to
#: whoever fills in the form.
MAX_REPEAT = 20
MAX_WARMUP = 5
#: How many runs may be in flight before the console stops starting more. The
#: cap is about this machine, not about politeness: every run is a subprocess
#: with a benchmark inside it.
MAX_ACTIVE_RUNS = 4

#: Where the console keeps its own notes about what it started. Beside the
#: results, never inside them: a sealed record is covered by `record_digest`
#: and must not grow a field because a page wanted a count.
LAUNCH_DIRNAME = ".launches"

_SUITE_PREFIX = "suite:"
_SCALARS = (str, int, float, bool)


@dataclass(frozen=True)
class LaunchSpec:
    """A validated request to start one run. Every field has been checked."""

    domain: str
    task_id: str
    platform: str
    target: str | None
    params: dict[str, Any]
    repeat: int = 1
    warmup: int = 0


def _suite_id(task_id: str) -> str:
    return task_id.removeprefix(_SUITE_PREFIX)


def _scalar_param(value: Any) -> bool:
    if isinstance(value, _SCALARS):
        return True
    return isinstance(value, list) and all(isinstance(item, _SCALARS) for item in value)


def _int_field(body: dict[str, Any], name: str, low: int, high: int) -> tuple[int | None, str]:
    raw = body.get(name, low)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None, f"{name} must be a whole number"
    if raw < low or raw > high:
        return None, f"{name} must be between {low} and {high}"
    return raw, ""


def validate_launch(body: dict[str, Any], configs_dir: Path) -> tuple[LaunchSpec | None, str]:
    """A LaunchSpec, or None and one sentence saying what was wrong.

    The sentence names the legal values wherever it can. A console that refuses
    without saying what would have worked is a guessing game.
    """
    from clousight_bench.core.registry import load_benchmark_suites, load_domains, load_evaluators

    domain = str(body.get("domain") or "")
    platform = str(body.get("platform") or "")
    task_id = str(body.get("task_id") or "")

    packs = load_domains()
    if domain not in packs:
        return None, f"unknown domain {domain!r}; installed: {', '.join(sorted(packs))}"
    adapters = packs[domain].adapters()
    if platform not in adapters:
        return None, (
            f"unknown platform {platform!r} for domain {domain!r}; installed: {', '.join(sorted(adapters))}"
        )

    if not task_id:
        return None, "a run needs a benchmark"
    # `gsm8k` and `suite:gsm8k` are the same benchmark. The record spells the
    # canonical one, so the normalisation happens here rather than downstream.
    task_id = task_id if task_id.startswith(_SUITE_PREFIX) else f"{_SUITE_PREFIX}{task_id}"
    suites = load_benchmark_suites()
    suite_id = _suite_id(task_id)
    if suite_id not in suites:
        return None, f"unknown benchmark {suite_id!r}; installed: {', '.join(sorted(suites))}"
    # What this can check: something installed knows how to score this
    # benchmark. What it deliberately does not check: whether this benchmark
    # belongs on this platform. No evaluator reads its ``product`` argument and
    # nothing in the registry maps a benchmark to a domain, so any rule here
    # would be invented — and an invented rule is the one that goes stale the
    # week a new adapter lands. The form shows where each benchmark has
    # actually run instead (``seen_platforms``), and the confirmation step
    # warns about a pairing nothing has run before.
    if not any(ev.supports(suite_id, platform) for ev in load_evaluators()):
        return None, (f"no installed evaluator scores benchmark {suite_id!r} — this run would fail at SCORE")

    target = body.get("target")
    if target is not None:
        if not isinstance(target, str) or not valid_target_name(target):
            return None, "target must be the name of a config in the configs directory"
        path = target_path(configs_dir, target)
        if path is None or not path.is_file():
            return None, f"no such target: {target!r}"

    params = body.get("params", {})
    if not isinstance(params, dict):
        return None, "params must be an object"
    for key, value in params.items():
        if not isinstance(key, str) or not _scalar_param(value):
            return None, (
                f"param {key!r} must be a string, number, boolean, or a list of those — "
                f"nested structures are not accepted"
            )

    repeat, why = _int_field(body, "repeat", 1, MAX_REPEAT)
    if repeat is None:
        return None, why
    warmup, why = _int_field(body, "warmup", 0, MAX_WARMUP)
    if warmup is None:
        return None, why

    return (
        LaunchSpec(
            domain=domain,
            task_id=task_id,
            platform=platform,
            target=target,
            params=dict(params),
            repeat=repeat,
            warmup=warmup,
        ),
        "",
    )


def _param_arg(key: str, value: Any) -> str:
    if isinstance(value, bool):  # before int: bool IS an int, and "True" is not a flag
        return f"{key}={'true' if value else 'false'}"
    if isinstance(value, list):
        return f"{key}={','.join(str(item) for item in value)}"
    return f"{key}={value}"


def launch_argv(spec: LaunchSpec, results_dir: Path, configs_dir: Path, run_id: str) -> list[str]:
    """The exact argv for this run. Built field by field, never parsed."""
    argv = [
        sys.executable,
        "-m",
        "clousight_bench",
        "run",
        "--domain",
        spec.domain,
        "--benchmark",
        _suite_id(spec.task_id),
        "--platform",
        spec.platform,
        "--results",
        str(results_dir),
        "--run-id",
        run_id,
    ]
    if spec.target is not None:
        path = target_path(configs_dir, spec.target)
        if path is not None:
            argv += ["--config", str(path)]
    for key, value in spec.params.items():
        argv += ["--param", _param_arg(key, value)]
    # A repeat of 1 is not a repeat: `csbench run` refuses --run-id together
    # with --repeat, because a plan is many runs and one id cannot name them.
    if spec.repeat > 1:
        argv += ["--repeat", str(spec.repeat)]
    if spec.warmup > 0:
        argv += ["--warmup", str(spec.warmup)]
    return argv


def launch_dir(results_dir: Path) -> Path:
    return Path(results_dir) / LAUNCH_DIRNAME


def record_launch(results_dir: Path, run_id: str, spec: LaunchSpec) -> Path:
    """Note what the console was asked to start, beside the results.

    This is the only place that knows which config file a run was given —
    a sealed record does not carry it — so it is also what lets a target say
    how many console-started runs used it.
    """
    directory = launch_dir(results_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run_id}.json"
    path.write_text(json.dumps({"run_id": run_id, **asdict(spec)}, ensure_ascii=False), encoding="utf-8")
    return path


def target_usage(results_dir: Path) -> dict[str, int]:
    """``target name -> how many runs this console started with it``."""
    directory = launch_dir(results_dir)
    if not directory.is_dir():
        return {}
    counts: dict[str, int] = {}
    for path in directory.glob("*.json"):
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # a half-written note is not worth failing a page over
        target = saved.get("target")
        if isinstance(target, str) and target:
            counts[target] = counts.get(target, 0) + 1
    return counts


def spawn_run(spec: LaunchSpec, results_dir: Path, configs_dir: Path, run_id: str) -> str:
    """Start the run detached and return its id.

    ``start_new_session`` puts the child in its own process group, so it
    survives the server being Ctrl-C'd — a benchmark half-way through a cloud
    provisioning should not die because someone closed the viewer.
    """
    argv = launch_argv(spec, results_dir, configs_dir, run_id)
    record_launch(results_dir, run_id, spec)
    log_path = launch_dir(results_dir) / f"{run_id}.log"
    # The argv carries the caller's params and target name, so it is sanitized
    # before it reaches a log line — a value that can inject a newline can
    # forge a second log entry.
    logger.info(
        "viewer: starting run %s: %s",
        run_id,
        sanitize_for_log(" ".join(argv[3:])),
    )
    with log_path.open("wb") as log:
        subprocess.Popen(  # noqa: S603 - argv is built from validated fields, no shell
            argv,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=Path.cwd(),
        )
    return run_id


def seen_pairs(results_dir: Path) -> dict[str, list[str]]:
    """``suite_id -> the platforms it has actually produced records on here``.

    Read off the sealed records because that is the only place the pairing is
    recorded at all. It is evidence, not permission: a benchmark that has never
    run on a platform may still be a perfectly good idea, and the form says so
    rather than refusing.
    """
    from clousight_bench.viewer.data import list_records

    pairs: dict[str, set[str]] = {}
    for record in list_records(results_dir):
        suite_id = str(record.get("suite_id") or "")
        adapter = str(record.get("adapter") or "")
        if suite_id and adapter:
            pairs.setdefault(suite_id, set()).add(adapter)
    return {suite: sorted(platforms) for suite, platforms in pairs.items()}


def launch_options(results_dir: Path | None = None) -> dict[str, Any]:
    """What the new-run form may offer: the installed registry, as choices.

    Plus, per benchmark, where it has actually run in this results directory —
    so the form can lead with the combinations that are known to work without
    pretending the others are forbidden.
    """
    from clousight_bench.core.registry import load_benchmark_suites, load_domains

    packs = load_domains()
    platforms_by_domain = {name: sorted(pack.adapters()) for name, pack in packs.items()}
    seen = seen_pairs(Path(results_dir)) if results_dir is not None else {}

    suites = []
    for suite_id, suite in sorted(load_benchmark_suites().items()):
        suites.append(
            {
                "suite_id": suite_id,
                "suite_version": str(getattr(suite, "suite_version", "")),
                "seen_platforms": seen.get(suite_id, []),
            }
        )

    domains = [
        {
            "domain": name,
            "description": str(getattr(pack, "description", "")),
            "platforms": [
                {"platform": p, "status": str(getattr(pack.adapters()[p], "status", "unknown"))}
                for p in platforms_by_domain[name]
            ],
        }
        for name, pack in sorted(packs.items())
    ]
    return {"domains": domains, "suites": suites, "max_repeat": MAX_REPEAT, "max_warmup": MAX_WARMUP}


def _find_launch_note(results_dir: Path, run_id: str) -> Path | None:
    """The launch note for ``run_id``, found by listing rather than by joining.

    ``run_id`` arrives in a URL. Every reader in this project resolves such a
    name against ``iterdir()`` so the path it returns is one the filesystem
    produced — see ``progress.locate_progress_dir`` for the same rule.
    """
    directory = launch_dir(results_dir)
    if not directory.is_dir():
        return None
    wanted = f"{run_id}.json"
    try:
        entries = list(directory.iterdir())
    except OSError:
        return None
    for entry in entries:
        if entry.name == wanted and entry.is_file():
            return entry
    return None


def prefill_from(results_dir: Path, run_id: str) -> dict[str, Any] | None:
    """The fields a new run would start from, taken from one that happened.

    The record carries the domain, the benchmark and the platform. It does not
    carry the config file it was handed or the params it was given — only the
    launch plane knows those, and only for runs this console started — so they
    are filled in when they are known and left out when they are not.

    ``repeat`` and ``warmup`` are deliberately absent. A batch size is a
    decision about one run; carrying it forward silently would turn one
    "like this" into twenty runs nobody asked for.
    """
    from clousight_bench.core.progress import valid_run_id
    from clousight_bench.viewer.data import load_record

    if not valid_run_id(run_id):
        return None
    record = load_record(results_dir, run_id)
    if record is None:
        return None
    raw_identity = record.get("identity")
    identity: dict[str, Any] = raw_identity if isinstance(raw_identity, dict) else {}
    seed: dict[str, Any] = {
        "domain": str(identity.get("domain") or ""),
        "task_id": str(identity.get("task_id") or ""),
        "platform": str(identity.get("adapter") or ""),
        "target": None,
        "params": {},
    }
    note = _find_launch_note(results_dir, run_id)
    if note is None:
        return seed
    try:
        saved = json.loads(note.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return seed
    if isinstance(saved.get("target"), str):
        seed["target"] = saved["target"]
    if isinstance(saved.get("params"), dict):
        seed["params"] = saved["params"]
    return seed
