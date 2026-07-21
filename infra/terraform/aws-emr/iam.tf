# Minimal IAM + security group for the EMR skeleton. Real deployments should
# scope these down; kept explicit here so the provisioning contract is readable.

data "aws_iam_policy_document" "emr_service_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["elasticmapreduce.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "emr_service" {
  name               = "clousight-bench-emr-service"
  assume_role_policy = data.aws_iam_policy_document.emr_service_assume.json
}

resource "aws_iam_role_policy_attachment" "emr_service" {
  role       = aws_iam_role.emr_service.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEMRServicePolicy_v2"
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "emr_ec2" {
  name               = "clousight-bench-emr-ec2"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "emr_ec2" {
  role       = aws_iam_role.emr_ec2.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonElasticMapReduceforEC2Role"
}

resource "aws_iam_instance_profile" "emr_profile" {
  name = "clousight-bench-emr-ec2"
  role = aws_iam_role.emr_ec2.name
}

resource "aws_security_group" "emr" {
  name        = "clousight-bench-emr"
  description = "Clousight Bench EMR skeleton SG"

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    project = "clousight-bench"
  }
}
