output "oss_bucket" {
  description = "OSS bucket name."
  value       = local.bucket_name
}

output "bucket_retained_on_destroy" {
  description = "Whether the bucket survives terraform destroy."
  value       = local.effective_retain
}

output "ram_user_name" {
  description = "The shared RAM user name."
  value       = alicloud_ram_user.bench.name
}

output "products_managed" {
  description = "Products whose policies are managed by this workspace."
  value       = var.enabled_products
}

output "access_key_id" {
  description = "AccessKey ID — use as ALIBABA_CLOUD_ACCESS_KEY_ID."
  value       = alicloud_ram_access_key.bench.id
}

output "access_key_secret" {
  description = "AccessKey secret. Sensitive — use: terraform output -raw access_key_secret"
  value       = alicloud_ram_access_key.bench.secret
  sensitive   = true
}

output "export_commands" {
  description = "Ready-to-paste shell export commands for the benchmark sub-user."
  value       = <<-EOT
    export ALIBABA_CLOUD_ACCESS_KEY_ID="${alicloud_ram_access_key.bench.id}"
    export ALIBABA_CLOUD_ACCESS_KEY_SECRET="${alicloud_ram_access_key.bench.secret}"
  EOT
  sensitive   = true
}

output "mock_base_url" {
  description = "Public URL of the FC mock tool server. Use as target.mock_base_url."
  value       = var.enable_mock_tools ? alicloud_fcv3_trigger.mock_tools_http[0].http_trigger[0].url_internet : ""
}

output "mock_token" {
  description = "Auth token for the mock tool server (X-Clousight-Token header)."
  value       = var.enable_mock_tools ? random_password.mock_token[0].result : ""
  sensitive   = true
}

output "csbench_config" {
  sensitive   = true
  description = <<-EOT
    Ready-to-use csbench run config. Write to file with:
      terraform output -raw csbench_config > /absolute/path/agent-runtime-aliyun.local.yaml
  EOT
  value = yamlencode({
    target = {
      provider      = "aliyun"
      region        = var.region
      mode          = "real"
      oss_bucket    = local.bucket_name
      mock_base_url = var.enable_mock_tools ? alicloud_fcv3_trigger.mock_tools_http[0].http_trigger[0].url_internet : ""
      mock_token    = var.enable_mock_tools ? random_password.mock_token[0].result : ""
      auth_env = {
        access_key_id     = "ALIBABA_CLOUD_ACCESS_KEY_ID"
        access_key_secret = "ALIBABA_CLOUD_ACCESS_KEY_SECRET"
      }
      eci_vswitch_id        = local.effective_vswitch_id
      eci_security_group_id = local.effective_sg_id
      eci_probe_role        = var.enable_probe ? alicloud_ram_role.eci_probe[0].role_name : ""
      ecs_image_id          = var.ecs_image_id
      ecs_instance_type     = var.ecs_instance_type
    }
    params = {}
  })
}

# ── ECI probe outputs ─────────────────────────────────────────────────────────

output "eci_probe_role_name" {
  description = "Name of the ECI instance RAM role for probe containers (empty when enable_probe = false)."
  value       = var.enable_probe ? alicloud_ram_role.eci_probe[0].role_name : ""
}

output "eci_probe_role_arn" {
  description = "ARN of the ECI instance RAM role for probe containers (empty when enable_probe = false)."
  value       = var.enable_probe ? alicloud_ram_role.eci_probe[0].arn : ""
}

output "probe_vswitch_id" {
  description = "VSwitch ID to use for ECI probe container groups (from create_vpc or supplied vswitch_id)."
  value       = local.effective_vswitch_id
}

output "probe_security_group_id" {
  description = "Security Group ID to use for ECI probe container groups (from create_vpc or supplied security_group_id)."
  value       = local.effective_sg_id
}

output "probe_nat_gateway_id" {
  description = "NAT Gateway ID providing SNAT egress for ECI probe containers (empty when enable_nat = false)."
  value       = var.enable_nat ? alicloud_nat_gateway.bench[0].id : ""
}

