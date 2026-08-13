variable "oss_bucket" {
  description = <<-EOT
    OSS bucket for ephemeral agent artifacts (clousight-bench/* prefix).
    Leave empty to auto-generate a name (clousight-bench-<random>).
    The generated name is persisted in state and reused on every subsequent apply.
    The bucket is never deleted on terraform destroy.
  EOT
  type        = string
  default     = ""
}

variable "ram_user_name" {
  description = "Shared RAM user for all clousight-bench benchmark tasks. One user per account."
  type        = string
  default     = "clousight-bench"
}

variable "region" {
  description = "Aliyun region (must match ALICLOUD_REGION env var)."
  type        = string
  default     = "cn-hangzhou"
}

variable "enabled_products" {
  description = <<-EOT
    List of cloud products to benchmark. Each becomes a policy ClousightBench-<product>.
    Supported: "AgentRun". Future: "EMR", "RDS", etc.
  EOT
  type        = list(string)
  default     = ["AgentRun"]
}

variable "create_vpc" {
  description = "Create a dedicated VPC + VSwitch + Security Group for AgentRun. Set false if you already have these and provide vpc_id / vswitch_id / security_group_id instead."
  type        = bool
  default     = true
}

variable "vpc_cidr" {
  description = "CIDR for the auto-created VPC."
  type        = string
  default     = "172.16.0.0/12"
}

variable "vswitch_cidr" {
  description = "CIDR for the auto-created VSwitch (must be within vpc_cidr)."
  type        = string
  default     = "172.16.0.0/24"
}

variable "vswitch_zone" {
  description = "Availability zone for the VSwitch. Leave empty to use the first available zone."
  type        = string
  default     = ""
}

variable "vpc_id" {
  description = "Existing VPC ID (only used when create_vpc = false)."
  type        = string
  default     = ""
}

variable "vswitch_id" {
  description = "Existing VSwitch ID (only used when create_vpc = false)."
  type        = string
  default     = ""
}

variable "security_group_id" {
  description = "Existing Security Group ID (only used when create_vpc = false)."
  type        = string
  default     = ""
}

variable "retain_bucket" {
  description = <<-EOT
    Set to true to keep the OSS bucket on terraform destroy.
    Default false — the bucket is a pure staging area (agent.zip only);
    benchmark results live in the local results/ directory, not in OSS.
    Automatically set to true when var.oss_bucket is provided (pre-existing bucket).
  EOT
  type        = bool
  default     = false
}

variable "enable_mock_tools" {
  description = <<-EOT
    Deploy the mock tool server as an Aliyun FC HTTP function.
    When true, a stable public URL is output as mock_base_url and written into
    csbench_config — no local server or tunnel needed.
  EOT
  type        = bool
  default     = true
}

variable "enable_data_plane" {
  description = <<-EOT
    Add AgentRun data-plane actions to the policy (InvokeRuntime / *Memory /
    ActivateTemplateMCP). Set false for control-plane smoke (T0.1/T0.2) only;
    set true when wiring T1–T5 data-plane tasks.
  EOT
  type        = bool
  default     = false
}

# ── ECI probe variables ───────────────────────────────────────────────────────

variable "enable_probe" {
  description = <<-EOT
    Create the ECI probe RAM role + policy so that ECI containers launched
    by the benchmark runner can write/read telemetry in OSS under the
    clousight-bench/* prefix.  Set false during initial bootstrap to skip
    ECI-specific IAM until you're ready for Phase B (live probe runs).
  EOT
  type        = bool
  default     = false
}

variable "enable_nat" {
  description = <<-EOT
    Create the NAT gateway + EIP that give the private ECI probe egress to the
    AgentRun public endpoint. Kept SEPARATE from enable_probe because the NAT/EIP
    bill hourly: turn it on only for the duration of an --probe eci session and
    tear it down after. Requires enable_probe = true and create_vpc = true.
  EOT
  type        = bool
  default     = false
}

variable "eci_probe_role_name" {
  description = "Name for the ECI instance RAM role assumed by probe containers."
  type        = string
  default     = "clousight-bench-eci-probe"
}

variable "telemetry_expiry_days" {
  description = <<-EOT
    Days before objects under the telemetry/ prefix are automatically deleted.
    Applies to all three OSS bucket variants (auto, auto_retained, user).
  EOT
  type        = number
  default     = 30
}

variable "session_state_expiry_days" {
  description = <<-EOT
    Days before objects under the session/ prefix are automatically deleted.
    Session state is short-lived; default 7 days is generous for debugging.
  EOT
  type        = number
  default     = 7
}

variable "dev_wheel_expiry_days" {
  description = <<-EOT
    Days before objects under the dev-wheels/ prefix are automatically deleted.
    Dev-wheel fallback artifacts are ephemeral (rebuilt per campaign); default 1.
  EOT
  type        = number
  default     = 1
}

variable "abort_incomplete_multipart_days" {
  description = "Days before incomplete multipart uploads are automatically aborted."
  type        = number
  default     = 1
}

variable "ecs_image_id" {
  description = <<-EOT
    Stock Aliyun Linux OS image id used to boot the ECS probe carrier, e.g.
    "aliyun_3_x64_20G_alibase_image". Find one with:
      aliyun ecs DescribeImages --RegionId <region> --OSType linux \
        --ImageOwnerAlias system --Architecture x86_64
    No private image / ACR needed — cloud-init pip-installs clousight-bench[probe]
    from the Aliyun VPC-internal PyPI mirror. Empty until set; required for
    --probe ecs. Written into csbench_config so live runs pick it up.
  EOT
  type        = string
  default     = ""
}

variable "ecs_instance_type" {
  description = <<-EOT
    ECS instance type for the probe carrier (2 vCPU / 4 GiB burstable economy
    class by default). Written into csbench_config.
  EOT
  type        = string
  default     = "ecs.e-c1m2.large"
}
