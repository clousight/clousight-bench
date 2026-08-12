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

variable "abort_incomplete_multipart_days" {
  description = "Days before incomplete multipart uploads are automatically aborted."
  type        = number
  default     = 1
}

variable "acr_namespace" {
  description = <<-EOT
    ACR personal edition namespace for the cb-probe image repository.
    Must be globally unique within the region; default matches the project name.
    Only used when enable_probe = true.
  EOT
  type        = string
  default     = "clousight-bench"
}

variable "eci_image" {
  description = <<-EOT
    Full registry-vpc image reference for the ECI probe container, e.g.
    "registry-vpc.cn-hangzhou.aliyuncs.com/clousight-bench/cb-probe:<tag>".
    Leave empty until the image has been built and pushed via build-push.sh.
    The operator sets this after the first push; it is written into csbench_config
    so live probe runs pick up the correct image URI without manual editing.
  EOT
  type        = string
  default     = ""
}
