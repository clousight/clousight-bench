# AWS EMR provisioning module (skeleton) for the bigdata-emr aws-emr adapter.
#
# The adapter's setup() runs `terraform apply` here to spin up a cluster, submit()
# adds a step, and teardown() runs `terraform destroy` so a forgotten cluster
# never keeps billing. Kept minimal on purpose: cluster shape is what a J1.x
# result is measured against, so it is folded into config_hash by the adapter.
#
# This module is NOT wired to run automatically; it documents the provisioning
# contract. Review costs before `terraform apply` against your own account.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type        = string
  description = "AWS region for the EMR cluster."
  default     = "ap-northeast-1"
}

variable "release_label" {
  type        = string
  description = "EMR release, e.g. emr-7.1.0."
  default     = "emr-7.1.0"
}

variable "master_instance_type" {
  type    = string
  default = "m5.xlarge"
}

variable "core_instance_type" {
  type    = string
  default = "m5.xlarge"
}

variable "core_instance_count" {
  type        = number
  description = "Number of core nodes. Drives both performance and cost."
  default     = 2
}

variable "log_uri" {
  type        = string
  description = "S3 URI for EMR logs, e.g. s3://my-bucket/emr-logs/."
}

variable "subnet_id" {
  type        = string
  description = "Subnet the cluster launches into."
}

variable "name" {
  type    = string
  default = "opencloudbench-emr"
}

resource "aws_emr_cluster" "bench" {
  name          = var.name
  release_label = var.release_label
  applications  = ["Spark", "Hadoop"]
  log_uri       = var.log_uri

  ec2_attributes {
    subnet_id                         = var.subnet_id
    instance_profile                  = aws_iam_instance_profile.emr_profile.arn
    emr_managed_master_security_group = aws_security_group.emr.id
    emr_managed_slave_security_group  = aws_security_group.emr.id
  }

  master_instance_group {
    instance_type = var.master_instance_type
  }

  core_instance_group {
    instance_type  = var.core_instance_type
    instance_count = var.core_instance_count
  }

  service_role = aws_iam_role.emr_service.arn

  # Auto-terminate protects against a forgotten cluster billing forever if
  # teardown() (terraform destroy) never runs.
  auto_termination_policy {
    idle_timeout = 3600
  }

  tags = {
    project = "opencloudbench"
    purpose = "benchmark"
  }
}

output "cluster_id" {
  value = aws_emr_cluster.bench.id
}

output "master_public_dns" {
  value = aws_emr_cluster.bench.master_public_dns
}
