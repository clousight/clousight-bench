import os
import stat
from pathlib import Path

import pytest

from clousight_bench.core.sandbox import ResourceLimits
from clousight_bench.core.workload import WorkloadEngine

pytestmark = pytest.mark.skipif(os.name != "posix", reason="rlimit is posix-only")


def _make_workload(tmp_path: Path, script: str) -> Path:
    (tmp_path / "manifest.yaml").write_text(
        "name: w\nversion: 0.1.0\nentrypoint: ./run.sh\n", encoding="utf-8")
    run = tmp_path / "run.sh"
    run.write_text("#!/usr/bin/env bash\n" + script, encoding="utf-8")
    run.chmod(run.stat().st_mode | stat.S_IEXEC)
    return tmp_path


def test_fsize_limit_kills_oversized_write(tmp_path):
    # Writing 50 MiB under a 1 MiB file-size limit: the write is killed by
    # SIGXFSZ and `set -e` aborts the script before it can report success.
    wl = _make_workload(
        tmp_path,
        'set -e\n'
        'head -c 52428800 /dev/zero > big.bin\n'
        'echo \'{"type":"result","ok":true}\'\n',
    )
    limits = ResourceLimits.from_target({"limits": {"fsize_mb": 1}})
    result = WorkloadEngine(wl).run(limits=limits)
    assert result.ok is False
    # the limit really bit: the file never reached the attempted size
    assert (tmp_path / "big.bin").stat().st_size <= 2 * (1 << 20)


def test_generous_default_limits_allow_normal_run(tmp_path):
    wl = _make_workload(
        tmp_path,
        'head -c 1048576 /dev/zero > small.bin 2>/dev/null\n'
        'echo \'{"type":"result","ok":true}\'\n',
    )
    result = WorkloadEngine(wl).run()  # default limits
    assert result.ok is True
