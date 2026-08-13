variable "region" {
  description = "AWS region (must match AWS_DEFAULT_REGION env var)."
  type        = string
  default     = "us-east-1"
}

variable "s3_bucket" {
  description = <<-EOT
    S3 bucket for ephemeral agent artifacts (clousight-bench/* prefix).
    Leave empty to auto-generate a name (clousight-bench-<random>).
    The generated name is persisted in state and reused on every subsequent apply.
  EOT
  type        = string
  default     = ""
}

variable "create_vpc" {
  description = "Create a dedicated VPC + Subnet + Security Group for AgentCore. Set false if you already have these."
  type        = bool
  default     = true
}

variable "vpc_cidr" {
  description = "CIDR for the auto-created VPC."
  type        = string
  default     = "172.16.0.0/16"
}

variable "subnet_cidr" {
  description = "CIDR for the auto-created subnet (must be within vpc_cidr)."
  type        = string
  default     = "172.16.0.0/24"
}

variable "enable_mock_tools" {
  description = <<-EOT
    Deploy the mock tool server as an AWS Lambda function URL.
    When true, a stable public URL is output as mock_base_url and written into
    csbench_config — no local server or tunnel needed.
  EOT
  type        = bool
  default     = true
}

variable "enable_probe" {
  description = <<-EOT
    Create the EC2 probe instance role + instance profile so that EC2 probe
    carrier instances launched by the benchmark runner can write/read telemetry
    in S3 under the clousight-bench/* prefix.  Set false during initial bootstrap
    to skip probe-specific IAM until you're ready for live probe runs.
  EOT
  type        = bool
  default     = false
}

variable "enable_nat" {
  description = <<-EOT
    Create the NAT Gateway + EIP that give the private subnet egress to public
    endpoints. Kept SEPARATE from enable_probe because the NAT/EIP bill hourly:
    turn it on only for the duration of a --probe session and tear it down after.
    Requires enable_probe = true and create_vpc = true.
  EOT
  type        = bool
  default     = false
}

variable "ec2_image_id" {
  description = <<-EOT
    AMI ID used to boot the EC2 probe carrier instance. Find one with:
      aws ec2 describe-images --owners amazon --filters Name=name,Values=al2023-ami-* \
        Name=architecture,Values=x86_64 --query 'sort_by(Images,&CreationDate)[-1].ImageId'
    Empty until set; required for --probe ec2. Written into csbench_config.
  EOT
  type        = string
  default     = ""
}

variable "ec2_instance_type" {
  description = "EC2 instance type for the probe carrier. Written into csbench_config."
  type        = string
  default     = "t3.small"
}

variable "telemetry_expiry_days" {
  description = "Days before objects under clousight-bench/telemetry/ are automatically deleted."
  type        = number
  default     = 30
}

variable "session_state_expiry_days" {
  description = "Days before objects under clousight-bench/state/ are automatically deleted."
  type        = number
  default     = 7
}

variable "dev_wheel_expiry_days" {
  description = "Days before objects under clousight-bench/dev-wheels/ are automatically deleted."
  type        = number
  default     = 1
}
