"""Terraform validate for the controller module (skips without terraform+init)."""

import shutil
import subprocess
from pathlib import Path

import pytest

_TF = shutil.which("terraform")
_TFDIR = Path(__file__).resolve().parents[1] / "infra" / "terraform" / "aliyun-iam"
_INITED = (_TFDIR / ".terraform").exists()


@pytest.mark.skipif(_TF is None or not _INITED, reason="terraform not installed / not initialized")
def test_controller_module_validates():
    proc = subprocess.run([_TF, "validate"], cwd=_TFDIR, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_controller_tf_declares_restricted_role_and_instance():
    # Static check (no terraform needed): the module wires the restricted role +
    # controller instance, gated by enable_controller.
    src = (_TFDIR / "controller.tf").read_text(encoding="utf-8")
    assert 'resource "alicloud_instance" "controller"' in src
    assert 'resource "alicloud_ram_role" "controller"' in src
    assert "ecs:DeleteInstance" in src  # self-destruct permission
    assert "vpc:DeleteNatGateway" in src  # NAT teardown permission
    assert "var.enable_controller ? 1 : 0" in src  # gated
    assert "clousight_bench.core.controller_main" in src  # runs cb-controller
