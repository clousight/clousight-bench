# Ephemeral ecs-resident campaign controller (prod profile).
#
# Brought up by `csbench submit` (MAIN account, once) alongside the NAT; runs the
# whole run-plan orchestration loop in-region, then self-destructs via the
# restricted role below. Gated by enable_controller so dev runs never create it.

variable "enable_controller" {
  type        = bool
  default     = false
  description = "Create the ecs-resident campaign controller (prod profile submit)."
}

variable "campaign_id" {
  type        = string
  default     = ""
  description = "Campaign id the controller polls for on OSS (stamped into env + tags)."
}

variable "controller_instance_type" {
  type        = string
  default     = "ecs.e-c1m2.large"
  description = "ECS type for the controller (orchestration is light; probes are serial)."
}

variable "controller_system_disk_category" {
  type        = string
  default     = "cloud_essd"
  description = "System disk category for the controller. Must be one the instance type + zone support (cloud_essd/cloud_auto for e-/u1-series in cn-hangzhou-b); the provider default cloud_efficiency is rejected by RunInstances."
}

variable "controller_install_docker" {
  type        = bool
  default     = false
  description = "Install + enable docker on the controller so it can act as a suite driver host (SWE-bench eval containers). Default false preserves the docker-less orchestration-only host."
}

variable "controller_system_disk_size" {
  type        = number
  default     = 40
  description = "Controller system disk size in GiB. Suite driver runs pulling eval images need headroom (e.g. 120); 40 is the pre-driver default."
}

variable "controller_docker_registry_mirror" {
  type        = string
  default     = ""
  description = "Docker registry mirror URL written to /etc/docker/daemon.json BEFORE docker starts (docker.io is throttled/unreachable from cn regions). Empty → no daemon.json."
}

variable "controller_hf_endpoint" {
  type        = string
  default     = ""
  description = "HF_ENDPOINT exported into the controller process env (e.g. https://hf-mirror.com — huggingface.co is unreachable from cn regions). Empty → not exported."
}

variable "controller_wheel_url" {
  type        = string
  default     = ""
  description = "Presigned OSS URL of the clousight-bench dev wheel (private pkg not on the mirror); empty → install from the public mirror."
}

variable "controller_extra_deps" {
  type        = list(string)
  default     = []
  description = "Requirement specs (probe + store extras) pip-installed from the mirror before the wheel (a presigned URL can't carry [extras])."
}

variable "controller_debug" {
  type        = bool
  default     = false
  description = "DEBUG/DEV ONLY: give the controller a public IP + SSH key for interactive debugging. PRODUCTION MUST leave this false (no public IP)."
}

variable "controller_ssh_public_key" {
  type        = string
  default     = ""
  description = "SSH public key for debug access; only used when controller_debug=true."
}

resource "alicloud_ecs_key_pair" "controller" {
  count         = var.controller_debug && var.controller_ssh_public_key != "" ? 1 : 0
  key_pair_name = "clousight-bench-controller-dbg"
  public_key    = var.controller_ssh_public_key
}

# DEBUG ONLY: open port 22 so we can SSH in to read cb-controller boot errors.
resource "alicloud_security_group_rule" "controller_ssh" {
  count             = var.controller_debug ? 1 : 0
  type              = "ingress"
  ip_protocol       = "tcp"
  port_range        = "22/22"
  security_group_id = alicloud_security_group.bench[0].id
  cidr_ip           = "0.0.0.0/0"
}

data "alicloud_images" "controller" {
  count       = var.enable_controller ? 1 : 0
  owners      = "system"
  name_regex  = "^aliyun_3_"
  most_recent = true
}

# Restricted delete role: ONLY this run's runtime + NAT/EIP/SNAT + self ECS + the
# bench OSS bucket. Never the MAIN account. This is what lets the controller reap
# itself on timeout with no laptop involvement.
resource "alicloud_ram_role" "controller" {
  count       = var.enable_controller ? 1 : 0
  role_name   = "clousight-bench-controller"
  description = "Restricted self-destruct role for the ecs-resident campaign controller."
  assume_role_policy_document = jsonencode({
    Version = "1"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = ["ecs.aliyuncs.com"] }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "alicloud_ram_policy" "controller" {
  count       = var.enable_controller ? 1 : 0
  policy_name = "clousight-bench-controller-teardown"
  policy_document = jsonencode({
    Version = "1"
    Statement = [
      {
        # Full task lifecycle: parity with the bench user's AgentRun grant so
        # the controller can provision → publish → invoke → delete a runtime.
        # (The hand-picked subset here missed agentrun:PublishRuntimeVersion and
        # InvokeRuntime → tasks died at EXECUTE with NoPermission/ImplicitDeny.)
        Effect   = "Allow"
        Action   = concat(local.agentrun_control_plane, local.agentrun_data_plane)
        Resource = "acs:agentrun:*:*:*"
      },
      {
        # ARMS read-only: T4.x trace/OTel probes read spans after invocation.
        Effect = "Allow"
        Action = [
          "arms:SearchTraces", "arms:GetTrace", "arms:GetMultipleTrace",
          "arms:ListTraceApps", "arms:SearchTraceAppByName", "arms:GetTraceApp",
          "arms:QueryMetricByPage", "arms:DescribeTraceLicenseKey",
        ]
        Resource = "*"
      },
      {
        # EIP RAM actions live under the vpc: prefix (EIP is part of the VPC
        # product), NOT eip: — granting eip:* left the reaper's UnassociateEip /
        # ReleaseEip ImplicitDenied, so delete_nat failed (NAT stayed Available,
        # EIP InUse) while delete_self (ecs:) succeeded. Live-diagnosed 2026-08-15.
        Effect = "Allow"
        Action = [
          "vpc:DeleteNatGateway", "vpc:DeleteSnatEntry", "vpc:DescribeNatGateways",
          "vpc:DescribeSnatTableEntries", "vpc:DescribeVpcs", "vpc:DescribeVSwitches",
          "vpc:UnassociateEipAddress", "vpc:ReleaseEipAddress", "vpc:DescribeEipAddresses",
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:DeleteInstance", "ecs:DescribeInstances", "ecs:DescribeSecurityGroups"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["oss:GetObject", "oss:PutObject", "oss:ListObjects", "oss:DeleteObject"]
        Resource = "*"
      },
    ]
  })
}

resource "alicloud_ram_role_policy_attachment" "controller" {
  count       = var.enable_controller ? 1 : 0
  role_name   = alicloud_ram_role.controller[0].role_name
  policy_name = alicloud_ram_policy.controller[0].policy_name
  policy_type = "Custom"
}

locals {
  _mirror = "https://mirrors.cloud.aliyuncs.com/pypi/simple/"
  # Dev-wheel path: install the extras' deps from the mirror, then the private
  # wheel from its presigned URL. Fallback (no wheel url): install from mirror by
  # name (only works once clousight-bench is published there).
  _install_lines = var.controller_wheel_url != "" ? concat(
    [for d in var.controller_extra_deps : "python3.11 -m pip install -i '${local._mirror}' '${d}'"],
    ["python3.11 -m pip install '${var.controller_wheel_url}'"]
    ) : [
    "python3.11 -m pip install -i '${local._mirror}' 'clousight-bench[probe,store]'"
  ]

  # Driver-host lines — the Python twin is build_controller_user_data in
  # ecs_carrier.py; keep the emitted shell lines byte-identical there.
  _hf_lines = var.controller_hf_endpoint != "" ? [
    "export HF_ENDPOINT='${var.controller_hf_endpoint}'",
  ] : []
  # daemon.json lands BEFORE docker is installed/started so the first pull
  # already goes through the in-region mirror.
  _docker_mirror_lines = var.controller_docker_registry_mirror != "" ? [
    "mkdir -p /etc/docker",
    "echo '{\"registry-mirrors\": [\"${var.controller_docker_registry_mirror}\"]}' > /etc/docker/daemon.json",
  ] : []
  _docker_install_lines = var.controller_install_docker ? [
    "yum install -y docker || dnf install -y docker",
    "systemctl enable --now docker",
  ] : []

  controller_user_data = base64encode(join("\n", concat(
    [
      "#!/bin/sh",
      "set -e",
      "export CB_CAMPAIGN_ID='${var.campaign_id}'",
      # local.bucket_name — NOT var.oss_bucket, which is "" on the default
      # random-suffix path, so the controller booted with an empty bucket name
      # (oss2 rejected it: "The bucket_name is invalid"), never writing any OSS.
      "export CB_OSS_BUCKET='${local.bucket_name}'",
      "export CB_REGION='${var.region}'",
      "export CB_RESULTS_DIR='/var/lib/cb/results'",
      "export CB_PLATFORM='aliyun-agentrun'",
      # Make the alibabacloud default credential chain use THIS instance's RAM role
      # (no static AK on the box) for OSS + AgentRun + ECS SDK calls.
      "export ALIBABA_CLOUD_ECS_METADATA='clousight-bench-controller'",
      # Point pip at the in-region mirror for the RUNTIME agent-artifact vendoring
      # (artifact.py pip-installs langchain/otel at execute time). PyPI is
      # throttled from cn-hangzhou; pip reads PIP_INDEX_URL natively.
      "export PIP_INDEX_URL='${local._mirror}'",
    ],
    local._hf_lines,
    local._docker_mirror_lines,
    local._docker_install_lines,
    [
      "yum install -y python3.11",
      "python3.11 -m ensurepip --upgrade",
    ],
    local._install_lines,
    ["exec python3.11 -m clousight_bench.core.controller_main"]
  )))
}

resource "alicloud_instance" "controller" {
  count         = var.enable_controller ? 1 : 0
  instance_name = "clousight-bench-controller-${var.campaign_id}"
  image_id      = data.alicloud_images.controller[0].images[0].id
  instance_type = var.controller_instance_type
  # e-/u1-series (and most current gens) do NOT support the provider-default
  # cloud_efficiency system disk in cn-hangzhou-b — only cloud_essd/cloud_auto.
  # Omitting this made RunInstances fail with InvalidSystemDiskCategory.
  system_disk_category = var.controller_system_disk_category
  system_disk_size     = var.controller_system_disk_size
  vswitch_id           = alicloud_vswitch.bench[0].id
  security_groups      = [alicloud_security_group.bench[0].id]
  role_name            = alicloud_ram_role.controller[0].role_name
  # Production: 0 (no public IP, VPC-internal, egress via NAT). Debug: 5 Mbps
  # public IP so we can SSH in and read cb-controller's boot errors.
  internet_max_bandwidth_out = var.controller_debug ? 5 : 0
  key_name                   = (var.controller_debug && var.controller_ssh_public_key != "") ? alicloud_ecs_key_pair.controller[0].key_pair_name : null
  instance_charge_type       = "PostPaid"
  user_data                  = local.controller_user_data
  tags = {
    campaign_id = var.campaign_id
    app         = "clousight-bench-controller"
  }
}
