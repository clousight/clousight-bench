import os
import stat
from pathlib import Path

import pytest

from clousight_bench.core.sandbox import SandboxViolation
from clousight_bench.core.workload import WorkloadEngine


def _make_workload(tmp_path: Path, script: str) -> Path:
    (tmp_path / "manifest.yaml").write_text(
        "name: w\nversion: 0.1.0\nentrypoint: ./run.sh\n", encoding="utf-8")
    run = tmp_path / "run.sh"
    run.write_text("#!/usr/bin/env bash\n" + script, encoding="utf-8")
    run.chmod(run.stat().st_mode | stat.S_IEXEC)
    return tmp_path


def test_artifact_path_traversal_rejected(tmp_path):
    if os.name != "posix":
        pytest.skip("posix shell workload")
    wl = _make_workload(tmp_path, 'echo \'{"type":"artifact","path":"../../etc/hostname"}\'\n'
                                  'echo \'{"type":"result","ok":true}\'\n')
    with pytest.raises(SandboxViolation):
        WorkloadEngine(wl).run()


def test_params_temp_file_cleaned_up(tmp_path):
    if os.name != "posix":
        pytest.skip("posix shell workload")
    wl = _make_workload(tmp_path, 'echo \'{"type":"result","ok":true}\'\n')
    import clousight_bench.core.workload as wlmod
    created = []
    real_ntf = wlmod.tempfile.NamedTemporaryFile

    def _spy(*a, **k):
        fh = real_ntf(*a, **k)
        created.append(fh.name)
        return fh

    wlmod.tempfile.NamedTemporaryFile = _spy
    try:
        WorkloadEngine(wl).run()
    finally:
        wlmod.tempfile.NamedTemporaryFile = real_ntf
    assert created and not any(os.path.exists(p) for p in created)
