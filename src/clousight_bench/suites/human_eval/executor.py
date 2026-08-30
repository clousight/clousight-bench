"""Sandboxed HumanEval candidate-code executor.

HumanEval scores a candidate by executing ``prompt + completion`` together with
the problem's self-contained ``check(candidate)`` test in a FRESH subprocess: a
zero exit code means every assertion passed.  The tests are stdlib-only and
carry no external data, so this needs no numpy/scipy and no network.

Isolation reuses the first-party :mod:`clousight_bench.core.sandbox` helper —
POSIX ``rlimit`` caps (CPU / address space / file size / fd count) applied in a
``preexec_fn`` plus a wall-clock ``timeout`` and a throwaway working directory.
As :mod:`core.sandbox` documents, this is defence-in-depth, NOT a container: it
bounds runaway CPU/memory/output, not filesystem or network access.  The offline
"reference" path executes the dataset's own CANONICAL solutions (vetted code we
bundle); running model-generated completions (endpoint mode) is untrusted and is
the caller's decision to make in their own isolated environment.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from clousight_bench.core.sandbox import ResourceLimits, posix_rlimit_preexec

# Per-candidate wall-clock ceiling.  HumanEval reference solutions finish in
# milliseconds; a generous default still bounds a pathological (e.g. infinite
# loop) completion.
DEFAULT_TIMEOUT_S: float = 30.0

_POSIX = os.name == "posix"

# Environment variables the candidate process is allowed to inherit.  Everything
# else — crucially every ``*_API_KEY`` / cloud credential in the parent env — is
# stripped, so untrusted model-generated code cannot read the operator's secrets
# out of ``os.environ`` and exfiltrate them (the sandbox has no network
# isolation).  ``HOME``/``TMPDIR`` are repointed at the throwaway working dir.
_ENV_PASSLIST = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SYSTEMROOT")


def _clean_env(workdir: str) -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_PASSLIST if k in os.environ}
    env.setdefault("PATH", os.defpath)
    env["HOME"] = workdir
    env["TMPDIR"] = workdir
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    """SIGKILL the whole process group (POSIX) so a candidate that forked
    grandchildren cannot leave runaways after a timeout; fall back to a plain
    kill elsewhere."""
    try:
        if _POSIX:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - non-POSIX fallback
            proc.kill()
    except (ProcessLookupError, OSError):  # pragma: no cover - already gone
        pass


def build_program(prompt: str, completion: str, test: str, entry_point: str) -> str:
    """Assemble the exact self-checking program HumanEval evaluates.

    ``prompt`` carries the imports + signature + docstring; ``completion`` is the
    function body (canonical solution or model output); ``test`` defines
    ``check(candidate)``; the trailing call runs it against the entry point.
    """
    return f"{prompt}{completion}\n\n{test}\n\ncheck({entry_point})\n"


def run_candidate(
    problem: dict[str, Any],
    completion: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    limits: ResourceLimits | None = None,
) -> dict[str, Any]:
    """Execute one candidate in a fresh, secret-stripped subprocess.

    Returns ``{"task_id", "passed": bool, "error": str}`` — ``error`` is a
    trimmed tail of stderr / the timeout marker when the candidate fails, empty
    on pass.  Never raises for a failing candidate: a crash / assertion / timeout
    is a legitimate benchmark outcome (``passed=False``), not an evaluator error.

    Isolation (defence-in-depth, NOT a container): a scrubbed environment
    (:func:`_clean_env`), POSIX ``rlimit`` caps, a wall-clock timeout, a new
    session so a timeout kills the whole process group, and a throwaway cwd.
    """
    program = build_program(problem["prompt"], completion, problem["test"], problem["entry_point"])
    limits = limits or ResourceLimits()
    with tempfile.TemporaryDirectory(prefix="csbench-humaneval-") as tmp:
        script = Path(tmp) / "candidate.py"
        script.write_text(program)
        proc = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=tmp,
            env=_clean_env(tmp),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=posix_rlimit_preexec(limits),
            start_new_session=_POSIX,
        )
        try:
            _, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            proc.communicate()  # reap the killed child so no zombie remains
            return {"task_id": problem["task_id"], "passed": False, "error": f"timeout>{timeout_s}s"}
        passed = proc.returncode == 0
        error = "" if passed else (stderr or "nonzero exit").strip()[-500:]
        return {"task_id": problem["task_id"], "passed": passed, "error": error}
