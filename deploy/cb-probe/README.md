# cb-probe container image

On Aliyun cn-hangzhou the ECI probe **must** run a prebuilt image pulled from the
account's own ACR: docker hub / github / pypi are throttled from the region, so a
stock public base image + boot-time install does not work (the container never
leaves "Pending" — verified live). The image bakes the probe + its deps, so the
running container fetches nothing from the public internet.

This is a **one-time** operator step (rebuild only when the probe code changes).
No CI pipeline and no local Docker are required — the simplest path is **Aliyun
Cloud Shell** (free, browser-based, Docker preinstalled, in-region network).

## Build + push once (Aliyun Cloud Shell)

1. Open Cloud Shell (the `>_` icon in the Aliyun console) and clone the repo.
2. In the ACR console (Personal Edition / 个人版), create the namespace
   `clousight-bench` and repo `cb-probe` (PRIVATE), and set a registry password
   (访问凭证). Terraform's `acr_repo_vpc_domain` output prints the expected pull
   ref, e.g. `registry-vpc.cn-hangzhou.aliyuncs.com/clousight-bench/cb-probe`.
3. Build + push:

```bash
export CB_REGISTRY=registry.cn-hangzhou.aliyuncs.com      # public push endpoint
export CB_IMAGE_REPO=clousight-bench/cb-probe             # <namespace>/cb-probe
export CB_REGISTRY_USER=<acr-username>
export CB_REGISTRY_PASSWORD=<acr-password>
./deploy/cb-probe/build-push.sh
```

`build-push.sh` builds `deploy/cb-probe/Dockerfile` (Aliyun pip mirror baked in),
pushes `:$(git rev-parse --short HEAD)`, and prints the reference.

## Wire it up

Set the **registry-vpc** reference (VPC-internal pull, no public IP needed) as the
probe image — terraform `var.eci_image` or csbench target `eci_image`:

```yaml
target:
  eci_image: registry-vpc.cn-hangzhou.aliyuncs.com/clousight-bench/cb-probe:<tag>
```

`csbench run-plan --probe eci` then launches a fully-private ECI (no public IP)
that pulls this image over the VPC-internal endpoint in ~30s.

## Other clouds

The Dockerfile is portable (override `BASE_IMAGE` / `PIP_INDEX_URL` build args).
For AWS/GCP, build the same image, push to that cloud's registry (ECR / Artifact
Registry), and point that vendor's carrier at it — the build is the same, only
the base/mirror/registry differ.
