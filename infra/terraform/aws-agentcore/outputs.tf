output "s3_bucket" {
  description = "S3 bucket name."
  value       = local.bucket_name
}

output "iam_user_name" {
  description = "The benchmark IAM user name."
  value       = aws_iam_user.bench.name
}

output "access_key_id" {
  description = "Access Key ID — use as AWS_ACCESS_KEY_ID."
  value       = aws_iam_access_key.bench.id
}

output "access_key_secret" {
  description = "Access Key Secret. Sensitive — use: terraform output -raw access_key_secret"
  value       = aws_iam_access_key.bench.secret
  sensitive   = true
}

output "export_commands" {
  description = "Ready-to-paste shell export commands for the benchmark IAM user."
  value       = <<-EOT
    export AWS_ACCESS_KEY_ID="${aws_iam_access_key.bench.id}"
    export AWS_SECRET_ACCESS_KEY="${aws_iam_access_key.bench.secret}"
  EOT
  sensitive   = true
}

output "mock_base_url" {
  description = "Public URL of the Lambda mock tool server. Use as target.mock_base_url."
  value       = var.enable_mock_tools ? aws_lambda_function_url.mock_tools[0].function_url : ""
}

output "mock_token" {
  description = "Auth token for the mock tool server (X-Clousight-Token header)."
  value       = var.enable_mock_tools ? random_password.mock_token[0].result : ""
  sensitive   = true
}

output "probe_subnet_id" {
  description = "Subnet ID to use for EC2 probe carrier instances (from create_vpc or empty)."
  value       = local.effective_subnet_id
}

output "probe_security_group_id" {
  description = "Security Group ID to use for EC2 probe carrier instances (from create_vpc or empty)."
  value       = local.effective_sg_id
}

output "probe_instance_profile" {
  description = "Instance profile name for the EC2 probe carrier (empty when enable_probe = false)."
  value       = var.enable_probe ? aws_iam_instance_profile.ec2_probe[0].name : ""
}

output "probe_nat_gateway_id" {
  description = "NAT Gateway ID providing egress for EC2 probe instances (empty when enable_nat = false)."
  value       = var.enable_nat ? aws_nat_gateway.bench[0].id : ""
}

output "csbench_config" {
  sensitive   = true
  description = <<-EOT
    Ready-to-use csbench run config. Write to file with:
      terraform output -raw csbench_config > /absolute/path/agent-runtime-aws.local.yaml
  EOT
  value = yamlencode({
    target = {
      provider   = "aws"
      region     = var.region
      mode       = "real"
      oss_bucket = local.bucket_name # key kept as oss_bucket for cross-provider parity
      mock_base_url = (
        var.enable_mock_tools ? aws_lambda_function_url.mock_tools[0].function_url : ""
      )
      mock_token = (
        var.enable_mock_tools ? random_password.mock_token[0].result : ""
      )
      auth_env = {
        access_key_id     = "AWS_ACCESS_KEY_ID"
        access_key_secret = "AWS_SECRET_ACCESS_KEY"
      }
      probe_subnet_id         = local.effective_subnet_id
      probe_security_group_id = local.effective_sg_id
      probe_instance_profile  = var.enable_probe ? aws_iam_instance_profile.ec2_probe[0].name : ""
      ec2_image_id            = var.ec2_image_id
      ec2_instance_type       = var.ec2_instance_type
    }
    params = {}
  })
}
