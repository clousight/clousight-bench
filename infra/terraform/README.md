# Terraform provisioning modules

One module per provider that needs real infrastructure stood up before a
benchmark (big-data clusters, databases, compute, messaging). Agent-runtime
adapters target always-on APIs and need none of this.

## Contract with adapters

A cluster adapter's lifecycle maps directly onto Terraform:

| Adapter hook | Terraform action |
|---|---|
| `setup()`    | `terraform init && terraform apply -auto-approve` |
| `submit()`   | read outputs (cluster id / DNS), submit a step, collect metrics |
| `teardown()` | `terraform destroy -auto-approve` |

`teardown()` must be safe to call even when `setup()` never ran, and the module
should carry a safety net (e.g. EMR `auto_termination_policy`) so a crash between
apply and destroy cannot bill forever.

## Modules

| Module | Domain / adapter | Status |
|---|---|---|
| `aws-emr/` | `bigdata-emr` / `aws-emr` | Skeleton — documents the provisioning contract; review costs before `apply`. |

Copy `<module>/terraform.tfvars.example` to `terraform.tfvars` and fill it for
your own account. Never commit real values.
