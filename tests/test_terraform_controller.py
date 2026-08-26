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


def test_controller_tf_docker_driver_knobs():
    # Static twin-check with build_controller_user_data: the driver-host knobs
    # exist as vars and emit the exact same shell lines the Python builder does.
    src = (_TFDIR / "controller.tf").read_text(encoding="utf-8")
    for var in (
        "controller_install_docker",
        "controller_system_disk_size",
        "controller_docker_registry_mirror",
        "controller_hf_endpoint",
        "controller_instance_type",
    ):
        assert f'variable "{var}"' in src, var
    # behavior-preserving defaults
    assert "default     = false" in src  # controller_install_docker
    assert "default     = 40" in src  # controller_system_disk_size
    # disk size wired on the instance (fmt-agnostic: key + var on one line)
    assert any(
        line.strip().startswith("system_disk_size")
        and line.strip().endswith("= var.controller_system_disk_size")
        for line in src.splitlines()
    )
    # same lines as build_controller_user_data (twin lockstep)
    assert "yum install -y docker || dnf install -y docker" in src
    assert "systemctl enable --now docker" in src
    assert "mkdir -p /etc/docker" in src
    assert "/etc/docker/daemon.json" in src and "registry-mirrors" in src
    assert "export HF_ENDPOINT='${var.controller_hf_endpoint}'" in src
