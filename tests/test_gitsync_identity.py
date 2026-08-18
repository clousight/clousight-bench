"""Maintainer identity gate for the remote-less gitsync helper.

GitHub attributes commits by author email. A personal Gmail on this machine
maps to a personal GitHub user, so gitsync must refuse any active `gh` account
other than clousight-dev. `repo` is local-only and stays ungated.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_GITSYNC = _ROOT / "scripts" / "gitsync.sh"
_ALLOWED_EMAIL = "306954191+clousight-dev@users.noreply.github.com"


def _run_gitsync(args: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_GITSYNC), *args],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _env_with_fake_gh(tmp_path: Path, login: str) -> dict[str, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "api" && "$2" == "user" ]]; then\n'
        f'  echo "{login}"\n'
        "  exit 0\n"
        "fi\n"
        'echo "unexpected gh $*" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    env = os.environ.copy()
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["CSBENCH_REPO"] = "clousight/clousight-bench"
    return env


def test_gitsync_syntax():
    proc = subprocess.run(["bash", "-n", str(_GITSYNC)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


def test_gitsync_repo_does_not_require_gh_identity(tmp_path: Path):
    env = _env_with_fake_gh(tmp_path, "legend91325")
    proc = _run_gitsync(["repo"], env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "clousight/clousight-bench"


@pytest.mark.parametrize("cmd", ["push", "pull", "merge", "checks", "status", "commit"])
def test_gitsync_rejects_personal_gh_account(tmp_path: Path, cmd: str):
    env = _env_with_fake_gh(tmp_path, "legend91325")
    proc = _run_gitsync([cmd], env=env)
    assert proc.returncode == 2
    assert "legend91325" in proc.stderr
    assert "gh auth switch --user clousight-dev" in proc.stderr


def test_gitsync_forces_clousight_dev_noreply_email():
    text = _GITSYNC.read_text(encoding="utf-8")
    assert 'ALLOWED_GH_USER="clousight-dev"' in text
    assert f'GIT_IDENTITY_EMAIL="{_ALLOWED_EMAIL}"' in text


def test_gitsync_refuses_push_to_main():
    text = _GITSYNC.read_text(encoding="utf-8")
    assert '[[ "$(branch)" != "main" ]]' in text
    assert "refusing to push 'main'" in text


def test_gitsync_merge_defaults_to_squash(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "api" && "$2" == "user" ]]; then\n'
        "  echo clousight-dev\n"
        "  exit 0\n"
        "fi\n"
        'if [[ "$1" == "pr" && "$2" == "merge" ]]; then\n'
        '  printf "gh-pr-merge:%s\\n" "$*"\n'
        "  exit 0\n"
        "fi\n"
        'echo "unexpected gh $*" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    env = os.environ.copy()
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["CSBENCH_REPO"] = "clousight/clousight-bench"
    proc = _run_gitsync(["merge", "12"], env=env)
    assert proc.returncode == 0, proc.stderr
    assert "--squash" in proc.stdout
    assert "12" in proc.stdout
