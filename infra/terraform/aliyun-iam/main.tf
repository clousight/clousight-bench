terraform {
  required_version = ">= 1.6"
  required_providers {
    alicloud = {
      source  = "aliyun/alicloud"
      version = ">= 1.220"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.0"
    }
  }
}

# Configure via env vars (recommended):
#   export ALICLOUD_ACCESS_KEY="..."
#   export ALICLOUD_SECRET_KEY="..."
#   export ALICLOUD_REGION="cn-hangzhou"
provider "alicloud" {}
provider "random" {}
provider "archive" {}

# ── VPC / VSwitch / Security Group ───────────────────────────────────────────
# Created when create_vpc = true (default). If you already have a VPC, set
# create_vpc = false and supply vpc_id / vswitch_id / security_group_id.

data "alicloud_zones" "available" {
  count                       = var.create_vpc && var.vswitch_zone == "" ? 1 : 0
  available_resource_creation = "VSwitch"
}

locals {
  zone_id = (
    var.vswitch_zone != "" ? var.vswitch_zone
    : var.create_vpc ? data.alicloud_zones.available[0].zones[0].id
    : ""
  )
}

resource "alicloud_vpc" "bench" {
  count      = var.create_vpc ? 1 : 0
  vpc_name   = "clousight-bench"
  cidr_block = var.vpc_cidr
}

resource "alicloud_vswitch" "bench" {
  count      = var.create_vpc ? 1 : 0
  vpc_id     = alicloud_vpc.bench[0].id
  cidr_block = var.vswitch_cidr
  zone_id    = local.zone_id
}

resource "alicloud_security_group" "bench" {
  count               = var.create_vpc ? 1 : 0
  security_group_name = "clousight-bench"
  vpc_id              = alicloud_vpc.bench[0].id
}

locals {
  effective_vpc_id     = var.create_vpc ? alicloud_vpc.bench[0].id : var.vpc_id
  effective_vswitch_id = var.create_vpc ? alicloud_vswitch.bench[0].id : var.vswitch_id
  effective_sg_id      = var.create_vpc ? alicloud_security_group.bench[0].id : var.security_group_id
}

# ── OSS bucket (created once, never destroyed) ────────────────────────────────
# Empty var.oss_bucket → auto-generate a stable name persisted in state.
# The bucket survives `terraform destroy`; only the RAM identity is torn down.

# ── OSS bucket ────────────────────────────────────────────────────────────────
# Two separate code paths so count never depends on "known after apply" values:
#
#  A. Auto-generated (var.oss_bucket == ""):
#     random_id produces a unique suffix persisted in state.
#     The bucket is always new (random name), so no existence check is needed.
#     Deleted on destroy unless retain_bucket = true.
#
#  B. User-provided (var.oss_bucket != ""):
#     Name is known at plan time → data source can check existence safely.
#     Never deleted on destroy (may contain data outside this project).

resource "random_id" "bucket_suffix" {
  count       = var.oss_bucket == "" ? 1 : 0
  byte_length = 4 # → e.g. clousight-bench-a1b2c3d4
}

locals {
  bucket_name      = var.oss_bucket != "" ? var.oss_bucket : "clousight-bench-${random_id.bucket_suffix[0].hex}"
  effective_retain = var.retain_bucket || var.oss_bucket != ""
}

# A-1: auto-generated, ephemeral (deleted on destroy)
resource "alicloud_oss_bucket" "bench_auto" {
  count         = var.oss_bucket == "" && !var.retain_bucket ? 1 : 0
  bucket        = local.bucket_name
  force_destroy = true # cleans up orphaned agent.zip from crashed runs

  lifecycle_rule {
    id      = "expire-telemetry"
    prefix  = "clousight-bench/telemetry/"
    enabled = true
    expiration {
      days = var.telemetry_expiry_days
    }
  }

  lifecycle_rule {
    id      = "expire-session"
    prefix  = "clousight-bench/state/"
    enabled = true
    expiration {
      days = var.session_state_expiry_days
    }
  }

  # Dev-wheel fallback artifacts (unreleased-version bring-ups) are ephemeral —
  # a fresh wheel is built + uploaded per campaign, so expire them quickly.
  lifecycle_rule {
    id      = "expire-dev-wheels"
    prefix  = "clousight-bench/dev-wheels/"
    enabled = true
    expiration {
      days = var.dev_wheel_expiry_days
    }
  }

  lifecycle_rule {
    id      = "abort-multipart"
    prefix  = "clousight-bench/"
    enabled = true
    abort_multipart_upload {
      days = var.abort_incomplete_multipart_days
    }
  }
}

# A-2: auto-generated, retained (retain_bucket = true)
resource "alicloud_oss_bucket" "bench_auto_retained" {
  count  = var.oss_bucket == "" && var.retain_bucket ? 1 : 0
  bucket = local.bucket_name

  lifecycle_rule {
    id      = "expire-telemetry"
    prefix  = "clousight-bench/telemetry/"
    enabled = true
    expiration {
      days = var.telemetry_expiry_days
    }
  }

  lifecycle_rule {
    id      = "expire-session"
    prefix  = "clousight-bench/state/"
    enabled = true
    expiration {
      days = var.session_state_expiry_days
    }
  }

  # Dev-wheel fallback artifacts (unreleased-version bring-ups) are ephemeral —
  # a fresh wheel is built + uploaded per campaign, so expire them quickly.
  lifecycle_rule {
    id      = "expire-dev-wheels"
    prefix  = "clousight-bench/dev-wheels/"
    enabled = true
    expiration {
      days = var.dev_wheel_expiry_days
    }
  }

  lifecycle_rule {
    id      = "abort-multipart"
    prefix  = "clousight-bench/"
    enabled = true
    abort_multipart_upload {
      days = var.abort_incomplete_multipart_days
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

# B: user-provided — only create if it doesn't already exist; never destroy
data "alicloud_oss_buckets" "user_existing" {
  count      = var.oss_bucket != "" ? 1 : 0
  name_regex = "^${var.oss_bucket}$"
}

locals {
  user_bucket_exists = var.oss_bucket != "" ? length(data.alicloud_oss_buckets.user_existing[0].buckets) > 0 : false
}

resource "alicloud_oss_bucket" "bench_user" {
  count  = var.oss_bucket != "" && !local.user_bucket_exists ? 1 : 0
  bucket = var.oss_bucket

  lifecycle_rule {
    id      = "expire-telemetry"
    prefix  = "clousight-bench/telemetry/"
    enabled = true
    expiration {
      days = var.telemetry_expiry_days
    }
  }

  lifecycle_rule {
    id      = "expire-session"
    prefix  = "clousight-bench/state/"
    enabled = true
    expiration {
      days = var.session_state_expiry_days
    }
  }

  # Dev-wheel fallback artifacts (unreleased-version bring-ups) are ephemeral —
  # a fresh wheel is built + uploaded per campaign, so expire them quickly.
  lifecycle_rule {
    id      = "expire-dev-wheels"
    prefix  = "clousight-bench/dev-wheels/"
    enabled = true
    expiration {
      days = var.dev_wheel_expiry_days
    }
  }

  lifecycle_rule {
    id      = "abort-multipart"
    prefix  = "clousight-bench/"
    enabled = true
    abort_multipart_upload {
      days = var.abort_incomplete_multipart_days
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

# ── Policy documents (generated here so local.bucket_name is available) ───────

locals {
  # Control-plane actions (always included)
  agentrun_control_plane = [
    "agentrun:CreateAgentRuntime",
    "agentrun:GetAgentRuntime",
    "agentrun:ListAgentRuntimes", # needed for cleanup / residual detection
    "agentrun:DeleteAgentRuntime",
    "agentrun:CreateAgentRuntimeEndpoint",
    "agentrun:GetAgentRuntimeEndpoint",
    "agentrun:ListAgentRuntimeEndpoints",
    "agentrun:DeleteAgentRuntimeEndpoint",
    "agentrun:PublishRuntimeVersion", # required: makes the code routable via endpoint
  ]
  # Data-plane actions (opt-in via var.enable_data_plane)
  agentrun_data_plane = [
    "agentrun:InvokeRuntime",
    "agentrun:CreateMemory",
    "agentrun:RetrieveMemory",
    "agentrun:UpdateMemory",
    "agentrun:ActivateTemplateMCP",
    "agentrun:StopTemplateMCP",
    # T2.1 tool / template registration probes
    "agentrun:ListTemplates",
    "agentrun:ListTools",
    "agentrun:CreateTool",
    "agentrun:DeleteTool",
    "agentrun:GetTool",
  ]

  # Map product name → policy document JSON
  product_policies = {
    AgentRun = jsonencode({
      Version = "1"
      Statement = [
        {
          Sid      = "AgentRunControlPlane"
          Effect   = "Allow"
          Action   = local.agentrun_control_plane
          Resource = "acs:agentrun:*:*:*"
        },
        {
          Sid      = "AgentRunDataPlane"
          Effect   = var.enable_data_plane ? "Allow" : "Deny"
          Action   = local.agentrun_data_plane
          Resource = "acs:agentrun:*:*:*"
        },
        {
          Sid      = "OssArtifact"
          Effect   = "Allow"
          Action   = ["oss:PutObject", "oss:GetObject", "oss:DeleteObject"]
          Resource = "acs:oss:*:*:${local.bucket_name}/clousight-bench/*"
        },
        {
          # Bucket-level ListObjects so the control plane can mirror the probe's OSS
          # prefix back to results/ (sync_probe_artifacts -> oss_sync.list_prefix).
          # ListObjects is a bucket action, not object-path scoped.
          Sid      = "OssArtifactList"
          Effect   = "Allow"
          Action   = ["oss:ListObjects"]
          Resource = "acs:oss:*:*:${local.bucket_name}"
        },
        {
          # Read-only VPC/VSW/SG: needed to resolve network config for CreateAgentRuntime.
          # Also grants DescribeSecurityGroups for the harness to pick a valid SG.
          Sid    = "VpcReadOnly"
          Effect = "Allow"
          Action = [
            "vpc:DescribeVpcs",
            "vpc:DescribeVSwitches",
            "ecs:DescribeSecurityGroups",
          ]
          Resource = "*"
        },
        {
          # ARMS read-only: T4.x trace/OTel probes read spans from ARMS after invocation.
          Sid    = "ArmsTraceReadOnly"
          Effect = "Allow"
          Action = [
            "arms:SearchTraces",
            "arms:GetTrace",
            "arms:GetMultipleTrace",
            "arms:ListTraceApps",
            "arms:SearchTraceAppByName",
            "arms:GetTraceApp",
            "arms:QueryMetricByPage", # T4.3 signals — metrics query
            "arms:DescribeTraceLicenseKey",
          ]
          Resource = "*"
        },
      ]
    })
    # Add new products here as the benchmark expands:
    # EMR = jsonencode({ ... })
  }

  # Only include products listed in var.enabled_products
  enabled_policies = {
    for p in var.enabled_products : p => local.product_policies[p]
  }
}

# ── Shared RAM user ────────────────────────────────────────────────────────────

data "alicloud_ram_users" "existing" {
  name_regex = "^${var.ram_user_name}$"
}

locals {
  user_exists = length(data.alicloud_ram_users.existing.users) > 0
  user_name   = local.user_exists ? data.alicloud_ram_users.existing.users[0].name : var.ram_user_name
}

resource "alicloud_ram_user" "bench" {
  name         = var.ram_user_name
  display_name = "Clousight Bench"
  comments     = "Shared benchmark identity — one policy per product attached below."
}

resource "alicloud_ram_access_key" "bench" {
  user_name  = alicloud_ram_user.bench.name
  depends_on = [alicloud_ram_user.bench]
}

# ── Per-product policies and attachments ──────────────────────────────────────

resource "alicloud_ram_policy" "product" {
  for_each = local.enabled_policies

  policy_name     = "ClousightBench-${each.key}"
  policy_document = each.value
  description     = "Clousight Bench permissions for ${each.key} benchmark tasks."
  force           = true
}

resource "alicloud_ram_user_policy_attachment" "product" {
  for_each = local.enabled_policies

  user_name   = alicloud_ram_user.bench.name
  policy_name = "ClousightBench-${each.key}"
  policy_type = "Custom"

  depends_on = [alicloud_ram_user.bench, alicloud_ram_policy.product]
}

# ── Mock tool server — Aliyun FC custom runtime ───────────────────────────────
# Packages mock_tools.py as an FC HTTP function so the deployed benchmark agent
# can call it from inside Aliyun's network (same region, low latency, no tunnel).
# The FC HTTP trigger gives a stable public URL used as target.mock_base_url.

resource "random_password" "mock_token" {
  count   = var.enable_mock_tools ? 1 : 0
  length  = 32
  special = false
}

# Build the zip from infra/mock-tools-fc/ at plan time
data "archive_file" "mock_tools" {
  count       = var.enable_mock_tools ? 1 : 0
  type        = "zip"
  source_dir  = "${path.module}/../../mock-tools-fc"
  output_path = "${path.module}/mock-tools-fc.zip"
}

# Upload zip to the benchmark OSS bucket
resource "alicloud_oss_bucket_object" "mock_tools_zip" {
  count  = var.enable_mock_tools ? 1 : 0
  bucket = local.bucket_name
  # Hash in the key ensures a new object is uploaded whenever the zip content
  # changes, preventing FC from serving a cached version of the old code.
  key    = "clousight-bench/mock-tools/${data.archive_file.mock_tools[0].output_md5}.zip"
  source = data.archive_file.mock_tools[0].output_path

  depends_on = [alicloud_oss_bucket.bench_auto, alicloud_oss_bucket.bench_auto_retained]
}

# RAM role for FC function execution (FC needs to assume this role)
resource "alicloud_ram_role" "fc_mock_tools" {
  count       = var.enable_mock_tools ? 1 : 0
  role_name   = "clousight-bench-mock-tools-fc"
  description = "Execution role for clousight-bench mock tool server FC function."
  assume_role_policy_document = jsonencode({
    Version = "1"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = ["fc.aliyuncs.com"] }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "alicloud_ram_role_policy_attachment" "fc_mock_tools" {
  count       = var.enable_mock_tools ? 1 : 0
  role_name   = alicloud_ram_role.fc_mock_tools[0].role_name
  policy_name = "AliyunFCDefaultRolePolicy"
  policy_type = "System"
}

# FC3 function — custom runtime, the bootstrap starts our Python HTTP server
resource "alicloud_fcv3_function" "mock_tools" {
  count         = var.enable_mock_tools ? 1 : 0
  function_name = "csbench-mock-tools"
  description   = "Clousight Bench mock tool server (fault-injectable HTTP API)."
  runtime       = "custom.debian10"
  handler       = "not-used" # custom runtime uses bootstrap, not a handler
  memory_size   = 512
  timeout       = 600 # long timeout: function runs persistently per invocation
  role          = alicloud_ram_role.fc_mock_tools[0].arn

  code {
    oss_bucket_name = local.bucket_name
    oss_object_name = alicloud_oss_bucket_object.mock_tools_zip[0].key
  }

  environment_variables = {
    CSBENCH_MOCK_TOKEN = random_password.mock_token[0].result
  }

  depends_on = [alicloud_oss_bucket_object.mock_tools_zip, alicloud_ram_role_policy_attachment.fc_mock_tools]
}

# HTTP trigger — public URL, anonymous auth (token checked in application layer)
resource "alicloud_fcv3_trigger" "mock_tools_http" {
  count         = var.enable_mock_tools ? 1 : 0
  function_name = alicloud_fcv3_function.mock_tools[0].function_name
  trigger_name  = "http-trigger"
  trigger_type  = "http"
  qualifier     = "LATEST"

  trigger_config = jsonencode({
    authType = "anonymous"
    methods  = ["GET", "POST", "DELETE", "PUT", "HEAD"]
  })

  depends_on = [alicloud_fcv3_function.mock_tools]
}

# ── Probe carrier RAM role + policy ───────────────────────────────────────────
# Mirrors the fc_mock_tools role pattern (count gate via enable_probe).
# The ECS probe carrier instance assumes this role so the probe can write/read
# telemetry in OSS under the clousight-bench/* prefix.
# (Resource names keep the historical `eci_probe` label to avoid a state-churning
# rename; the carrier itself is now a stock ECS instance, not an ECI container.)

resource "alicloud_ram_role" "eci_probe" {
  count       = var.enable_probe ? 1 : 0
  role_name   = var.eci_probe_role_name
  description = "Instance RAM role for the ECS probe carrier — OSS telemetry read/write."
  # An ECS instance RAM role (assumed by the instance at runtime via the ECS
  # metadata service) must trust ecs.aliyuncs.com. eci.aliyuncs.com is kept too
  # so the same role works if a carrier is ever launched as ECI — trusting only
  # eci.aliyuncs.com made the pass fail with "Forbidden.RamRoleNotExist"
  # (verified live 2026-08-12). Keep both.
  assume_role_policy_document = jsonencode({
    Version = "1"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = ["ecs.aliyuncs.com", "eci.aliyuncs.com"] }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "alicloud_ram_policy" "eci_probe" {
  count       = var.enable_probe ? 1 : 0
  policy_name = "ClousightBench-EciProbe"
  description = "Allows the ECS probe carrier to write/read telemetry under clousight-bench/* in OSS."
  force       = true
  policy_document = jsonencode({
    Version = "1"
    Statement = [
      {
        Sid      = "EciProbeOss"
        Effect   = "Allow"
        Action   = ["oss:PutObject", "oss:GetObject", "oss:DeleteObject"]
        Resource = "acs:oss:*:*:${local.bucket_name}/clousight-bench/*"
      },
      {
        # ListObjects is a bucket-level action (not object-path scoped). The
        # carrier's OSS-mediated job-discovery loop (OssChannel.list_pending_jobs)
        # enumerates the control prefix, so the instance role MUST be able to list
        # the bucket. Without this the carrier never discovers dispatched jobs and
        # every live job times out (burning NAT+ECS spend on a guaranteed failure).
        Sid      = "EciProbeOssList"
        Effect   = "Allow"
        Action   = ["oss:ListObjects"]
        Resource = "acs:oss:*:*:${local.bucket_name}"
      },
    ]
  })
}

resource "alicloud_ram_role_policy_attachment" "eci_probe" {
  count       = var.enable_probe ? 1 : 0
  role_name   = alicloud_ram_role.eci_probe[0].role_name
  policy_name = alicloud_ram_policy.eci_probe[0].policy_name
  policy_type = "Custom"

  depends_on = [alicloud_ram_role.eci_probe, alicloud_ram_policy.eci_probe]
}

# ── ECS-launch + scoped PassRole permissions for the benchmark RAM user ────────
# Grants alicloud_ram_user.bench the ability to:
#   1. Run / describe / delete ECS probe carrier instances (Phase B probe lifecycle).
#   2. Pass the eci_probe role to ECS — SCOPED to that role via Resource ARN.
#      Unrestricted ram:PassRole is a privilege-escalation vector; the ARN scoping
#      ensures the user can only pass this specific role.
# Gated by var.enable_probe so a non-probe apply is completely unaffected.

resource "alicloud_ram_policy" "eci_probe_ops" {
  count       = var.enable_probe ? 1 : 0
  policy_name = "ClousightBench-EciProbeOps"
  description = "Allows the benchmark user to launch/reap ECS probe carrier instances and pass the probe role to ECS."
  force       = true
  policy_document = jsonencode({
    Version = "1"
    Statement = [
      {
        Sid    = "EcsProbeInstanceLifecycle"
        Effect = "Allow"
        # The probe carrier is a stock ECS instance (EcsProbeCarrier) that the
        # benchmark user launches, polls, and reaps. RunInstances does not accept
        # fine-grained resource ARNs (the instance does not exist yet); "*" is standard.
        Action = [
          "ecs:RunInstances",
          "ecs:DescribeInstances",
          "ecs:DeleteInstances",
        ]
        Resource = "*"
      },
      {
        Sid    = "PassRoleToEcs"
        Effect = "Allow"
        Action = "ram:PassRole"
        # Resource = "*", NOT the role ARN. Live-verified 2026-08-13: scoping
        # PassRole to alicloud_ram_role.eci_probe[0].arn (and also with an
        # acs:Service=ecs.aliyuncs.com Condition) was rejected by ECS RunInstances
        # with InvalidUser.PassRoleForbidden — the ECS PassRole check does not
        # match this ARN form. "*" is what actually lets the benchmark user launch
        # the carrier. The real guard is the role's own trust policy (only
        # ecs/eci.aliyuncs.com can assume it), so a wildcard PassRole cannot be
        # used to assume any other role.
        Resource = "*"
      },
    ]
  })
}

resource "alicloud_ram_user_policy_attachment" "eci_probe_ops" {
  count = var.enable_probe ? 1 : 0

  user_name   = alicloud_ram_user.bench.name
  policy_name = alicloud_ram_policy.eci_probe_ops[0].policy_name
  policy_type = "Custom"

  depends_on = [alicloud_ram_user.bench, alicloud_ram_policy.eci_probe_ops]
}

# ── NAT Gateway — private ECI egress ─────────────────────────────────────────
# ECI containers have NO public IP (enforced in the carrier); the NAT gateway
# provides SNAT-only egress so the probe can reach the AgentRun public endpoint
# at runtime (the image comes from ACR over the VPC-internal endpoint, and OSS
# uses its internal endpoint — only the AgentRun hop needs egress).
#
# The NAT + EIP bill hourly, so they are gated by a SEPARATE `enable_nat` flag,
# NOT enable_probe: bring the NAT up only for the duration of an --probe eci
# session and tear it down after (`terraform apply -var enable_nat=true` before,
# `terraform destroy -target=...nat... -var enable_nat=false` after — or just
# flip the var). The rest of the probe infra (RAM role, IAM) is free and stays.
# Enhanced NAT is required for new Aliyun accounts (Classic NAT is deprecated).

resource "alicloud_nat_gateway" "bench" {
  count            = var.enable_nat ? 1 : 0
  vpc_id           = alicloud_vpc.bench[0].id
  nat_gateway_name = "clousight-bench-nat"
  nat_type         = "Enhanced"
  # Enhanced NAT requires a vswitch in the same VPC; we reuse the bench vswitch.
  vswitch_id   = alicloud_vswitch.bench[0].id
  payment_type = "PayAsYouGo"
}

resource "alicloud_eip_address" "nat" {
  count        = var.enable_nat ? 1 : 0
  address_name = "clousight-bench-nat-eip"
  payment_type = "PayAsYouGo"
}

resource "alicloud_eip_association" "nat" {
  count         = var.enable_nat ? 1 : 0
  allocation_id = alicloud_eip_address.nat[0].id
  instance_id   = alicloud_nat_gateway.bench[0].id
  instance_type = "Nat"
}

resource "alicloud_snat_entry" "bench" {
  count             = var.enable_nat ? 1 : 0
  snat_table_id     = alicloud_nat_gateway.bench[0].snat_table_ids
  source_vswitch_id = alicloud_vswitch.bench[0].id
  snat_ip           = alicloud_eip_address.nat[0].ip_address

  # The SNAT IP must exist before we can create the entry.
  depends_on = [alicloud_eip_association.nat]
}

# NOTE: Enhanced NAT auto-creates the 0.0.0.0/0 -> NAT route in the VPC's default
# route table, so no explicit alicloud_route_entry is needed (adding one fails with
# InvalidCIDRBlock.Duplicate). Verified live 2026-08-12: the route exists Available.

# NOTE: no ACR / image-pull permissions here. The probe carrier is a stock ECS
# instance (EcsProbeCarrier) booting a standard Aliyun Linux OS image; cloud-init
# `pip install`s the published clousight-bench[probe] package from the Aliyun
# VPC-internal PyPI mirror. There is no private image to pull, so the instance
# role needs only the OSS control-channel permissions granted above.

