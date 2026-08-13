# ── S3 bucket ─────────────────────────────────────────────────────────────────
# Two code paths so count never depends on "known after apply" values:
#
#  A. Auto-generated (var.s3_bucket == ""):
#     random_id produces a unique suffix persisted in state.
#     The bucket is always new (random name), no existence check needed.
#
#  B. User-provided (var.s3_bucket != ""):
#     Name is known at plan time; never deleted on destroy.

resource "random_id" "bucket_suffix" {
  count       = var.s3_bucket == "" ? 1 : 0
  byte_length = 4 # → e.g. clousight-bench-a1b2c3d4
}

locals {
  bucket_name = var.s3_bucket != "" ? var.s3_bucket : "clousight-bench-${random_id.bucket_suffix[0].hex}"
}

resource "aws_s3_bucket" "bench" {
  bucket        = local.bucket_name
  force_destroy = var.s3_bucket == "" # only auto-generated buckets are auto-destroyed
  tags = {
    project = "clousight-bench"
    purpose = "benchmark"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "bench" {
  bucket = aws_s3_bucket.bench.id

  rule {
    id     = "expire-telemetry"
    status = "Enabled"
    filter {
      prefix = "clousight-bench/telemetry/"
    }
    expiration {
      days = var.telemetry_expiry_days
    }
  }

  rule {
    id     = "expire-session"
    status = "Enabled"
    filter {
      prefix = "clousight-bench/state/"
    }
    expiration {
      days = var.session_state_expiry_days
    }
  }

  rule {
    id     = "expire-dev-wheels"
    status = "Enabled"
    filter {
      prefix = "clousight-bench/dev-wheels/"
    }
    expiration {
      days = var.dev_wheel_expiry_days
    }
  }

  rule {
    id     = "abort-multipart"
    status = "Enabled"
    filter {
      prefix = "clousight-bench/"
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# ── IAM user + access key (benchmark identity) ─────────────────────────────────

resource "aws_iam_user" "bench" {
  name = "clousight-bench"
  tags = {
    project = "clousight-bench"
    purpose = "benchmark"
  }
}

resource "aws_iam_access_key" "bench" {
  user = aws_iam_user.bench.name
}

# ── IAM policy for the benchmark user ─────────────────────────────────────────

data "aws_iam_policy_document" "bench" {
  # Bedrock AgentCore control- and data-plane
  statement {
    sid    = "BedrockAgentCore"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:CreateAgentRuntime",
      "bedrock-agentcore:GetAgentRuntime",
      "bedrock-agentcore:ListAgentRuntimes",
      "bedrock-agentcore:DeleteAgentRuntime",
      "bedrock-agentcore:CreateAgentRuntimeEndpoint",
      "bedrock-agentcore:GetAgentRuntimeEndpoint",
      "bedrock-agentcore:ListAgentRuntimeEndpoints",
      "bedrock-agentcore:DeleteAgentRuntimeEndpoint",
      "bedrock-agentcore:InvokeAgentRuntime",
    ]
    resources = ["*"]
  }

  # S3 object CRUD on the bench prefix
  statement {
    sid    = "S3ObjectCRUD"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:GetObject",
      "s3:DeleteObject",
    ]
    resources = ["arn:aws:s3:::${local.bucket_name}/clousight-bench/*"]
  }

  # S3 ListBucket on the bucket itself (bucket-level action)
  statement {
    sid       = "S3ListBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${local.bucket_name}"]
  }

  # X-Ray read-only (T4.x trace probes)
  statement {
    sid    = "XRayReadOnly"
    effect = "Allow"
    actions = [
      "xray:GetTraceSummaries",
      "xray:BatchGetTraces",
    ]
    resources = ["*"]
  }

  # CloudWatch read-only (T4.3 signals — metrics query)
  statement {
    sid    = "CloudWatchReadOnly"
    effect = "Allow"
    actions = [
      "cloudwatch:GetMetricData",
    ]
    resources = ["*"]
  }

  # EC2 probe lifecycle (only when enable_probe = true)
  dynamic "statement" {
    for_each = var.enable_probe ? [1] : []
    content {
      sid    = "EC2ProbeInstanceLifecycle"
      effect = "Allow"
      actions = [
        "ec2:RunInstances",
        "ec2:DescribeInstances",
        "ec2:TerminateInstances",
        "ec2:CreateTags",
      ]
      resources = ["*"]
    }
  }

  # iam:PassRole scoped to the probe instance role (only when enable_probe = true)
  dynamic "statement" {
    for_each = var.enable_probe ? [1] : []
    content {
      sid    = "PassRoleToEC2"
      effect = "Allow"
      actions = [
        "iam:PassRole",
      ]
      # Scoped to the specific probe instance role ARN for least privilege.
      # The role's own trust policy (trusting ec2.amazonaws.com) is the real guard.
      resources = [aws_iam_role.ec2_probe[0].arn]
    }
  }
}

resource "aws_iam_policy" "bench" {
  name        = "ClousightBench"
  description = "Clousight Bench permissions — AgentCore benchmark identity."
  policy      = data.aws_iam_policy_document.bench.json
}

resource "aws_iam_user_policy_attachment" "bench" {
  user       = aws_iam_user.bench.name
  policy_arn = aws_iam_policy.bench.arn
}

# ── EC2 probe instance role + instance profile ─────────────────────────────────
# Mirrors the aliyun eci_probe role pattern (count gate via enable_probe).

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ec2_probe" {
  count              = var.enable_probe ? 1 : 0
  name               = "clousight-bench-ec2-probe"
  description        = "Instance role for the EC2 probe carrier — S3 telemetry read/write."
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
  tags = {
    project = "clousight-bench"
  }
}

data "aws_iam_policy_document" "ec2_probe" {
  count = var.enable_probe ? 1 : 0

  statement {
    sid    = "EC2ProbeS3"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:GetObject",
      "s3:DeleteObject",
    ]
    resources = ["arn:aws:s3:::${local.bucket_name}/clousight-bench/*"]
  }

  statement {
    sid       = "EC2ProbeS3List"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${local.bucket_name}"]
  }
}

resource "aws_iam_policy" "ec2_probe" {
  count       = var.enable_probe ? 1 : 0
  name        = "ClousightBench-EC2Probe"
  description = "Allows the EC2 probe carrier to write/read telemetry under clousight-bench/* in S3."
  policy      = data.aws_iam_policy_document.ec2_probe[0].json
}

resource "aws_iam_role_policy_attachment" "ec2_probe" {
  count      = var.enable_probe ? 1 : 0
  role       = aws_iam_role.ec2_probe[0].name
  policy_arn = aws_iam_policy.ec2_probe[0].arn
}

resource "aws_iam_instance_profile" "ec2_probe" {
  count = var.enable_probe ? 1 : 0
  name  = "clousight-bench-ec2-probe"
  role  = aws_iam_role.ec2_probe[0].name
}

# ── Lambda mock tools ─────────────────────────────────────────────────────────
# Packages mock_tools/handler.py as a Lambda function with a public function URL.
# The function URL (auth_type = NONE) gives a stable public HTTPS endpoint used
# as target.mock_base_url. Token auth is checked in the application layer.

resource "random_password" "mock_token" {
  count   = var.enable_mock_tools ? 1 : 0
  length  = 32
  special = false
}

data "archive_file" "mock_tools" {
  count       = var.enable_mock_tools ? 1 : 0
  type        = "zip"
  source_dir  = "${path.module}/../mock-tools-lambda"
  output_path = "${path.module}/mock-tools-lambda.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_mock_tools" {
  count              = var.enable_mock_tools ? 1 : 0
  name               = "clousight-bench-mock-tools-lambda"
  description        = "Execution role for the clousight-bench mock tool server Lambda."
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_mock_tools_basic" {
  count      = var.enable_mock_tools ? 1 : 0
  role       = aws_iam_role.lambda_mock_tools[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "mock_tools" {
  count            = var.enable_mock_tools ? 1 : 0
  function_name    = "csbench-mock-tools"
  description      = "Clousight Bench mock tool server (fault-injectable HTTP API)."
  role             = aws_iam_role.lambda_mock_tools[0].arn
  filename         = data.archive_file.mock_tools[0].output_path
  source_code_hash = data.archive_file.mock_tools[0].output_base64sha256
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      CSBENCH_MOCK_TOKEN = random_password.mock_token[0].result
    }
  }

  tags = {
    project = "clousight-bench"
    purpose = "mock-tools"
  }

  depends_on = [aws_iam_role_policy_attachment.lambda_mock_tools_basic]
}

resource "aws_lambda_function_url" "mock_tools" {
  count              = var.enable_mock_tools ? 1 : 0
  function_name      = aws_lambda_function.mock_tools[0].function_name
  authorization_type = "NONE"
}

# ── VPC / Subnet / Security Group ─────────────────────────────────────────────
# Created when create_vpc = true (default). If you already have a VPC, set
# create_vpc = false and supply existing ids via a custom variables block.

resource "aws_vpc" "bench" {
  count      = var.create_vpc ? 1 : 0
  cidr_block = var.vpc_cidr
  tags = {
    Name    = "clousight-bench"
    project = "clousight-bench"
  }
}

resource "aws_subnet" "bench" {
  count      = var.create_vpc ? 1 : 0
  vpc_id     = aws_vpc.bench[0].id
  cidr_block = var.subnet_cidr
  tags = {
    Name    = "clousight-bench"
    project = "clousight-bench"
  }
}

resource "aws_security_group" "bench" {
  count       = var.create_vpc ? 1 : 0
  name        = "clousight-bench"
  description = "Clousight Bench benchmark security group — egress all."
  vpc_id      = aws_vpc.bench[0].id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "clousight-bench"
    project = "clousight-bench"
  }
}

locals {
  effective_subnet_id = var.create_vpc ? aws_subnet.bench[0].id : ""
  effective_sg_id     = var.create_vpc ? aws_security_group.bench[0].id : ""
}

# ── NAT Gateway — private subnet egress ───────────────────────────────────────
# Provides SNAT-only egress so the EC2 probe can reach public endpoints.
# Gated by a SEPARATE enable_nat flag (hourly billing), NOT enable_probe.
# Bring the NAT up only for the duration of a --probe session and tear it down
# after: `terraform apply -var enable_nat=true` / `terraform destroy -target=...`

resource "aws_internet_gateway" "bench" {
  count  = var.enable_nat ? 1 : 0
  vpc_id = aws_vpc.bench[0].id
  tags = {
    Name    = "clousight-bench-igw"
    project = "clousight-bench"
  }
}

resource "aws_eip" "nat" {
  count  = var.enable_nat ? 1 : 0
  domain = "vpc"
  tags = {
    Name    = "clousight-bench-nat-eip"
    project = "clousight-bench"
  }
}

resource "aws_nat_gateway" "bench" {
  count         = var.enable_nat ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.bench[0].id
  tags = {
    Name    = "clousight-bench-nat"
    project = "clousight-bench"
  }
  depends_on = [aws_internet_gateway.bench]
}

resource "aws_route_table" "private" {
  count  = var.enable_nat ? 1 : 0
  vpc_id = aws_vpc.bench[0].id
  tags = {
    Name    = "clousight-bench-private-rt"
    project = "clousight-bench"
  }
}

resource "aws_route" "private_nat" {
  count                  = var.enable_nat ? 1 : 0
  route_table_id         = aws_route_table.private[0].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.bench[0].id
}

resource "aws_route_table_association" "private" {
  count          = var.enable_nat ? 1 : 0
  subnet_id      = aws_subnet.bench[0].id
  route_table_id = aws_route_table.private[0].id
}
