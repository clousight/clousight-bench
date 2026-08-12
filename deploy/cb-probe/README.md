# cb-probe container image (OPTIONAL fast path)

**You do not need to build this image.** By default the in-region probe runs on a
**stock public base image** (`python:3.12`) and installs `clousight_bench[probe]`
from the public github archive tarball at container boot — no build, no registry,
vendor-neutral (the same bootstrap works on Aliyun ECI, AWS Fargate, GCP Cloud
Run, …). See `EciProbeCarrier._build_create_request`.

This directory is an **optional** pre-built-image path: bake the probe into an
image so the ECI skips the ~30–60s boot-time `pip install` (faster cold start).
It is a per-registry optimization, not a requirement, and it is **not tied to
ACR** — push the image to whatever registry your target cloud pulls from and set
`eci_image` to it.

## What the image contains

`python:3.12-slim` + the `clousight_bench` package with the `probe` extra
(`requests`, `oss2`) — no LangChain/OTel/agent bundle. Entrypoint:

```
python -m clousight_bench.domains.agent_runtime.probe.agent_loop
```

which reads these env vars at runtime (injected by the run-plan hook
`start_campaign_probe`):

| Variable | Required | Description |
|---|---|---|
| `CB_PROBE_BUCKET` | yes | OSS bucket name |
| `CB_PROBE_REGION` | yes | Region ID, e.g. `cn-hangzhou` |
| `CB_PROBE_CONTROL_PREFIX` | yes | Per-campaign control prefix (campaign ID) |
| `CB_PROBE_TOKEN` | no | Optional bearer token |

## Building and pushing (only if you want the fast path)

No local Docker required if you build in CI (a GitHub Actions job with `docker
buildx`). `build-push.sh` also works from any machine with Docker. It is
registry-agnostic — point it at Aliyun ACR, AWS ECR, GHCR, etc.:

| Variable | Required | Description |
|---|---|---|
| `CB_REGISTRY` | yes | Registry host, e.g. `registry.cn-hangzhou.aliyuncs.com` or `ghcr.io` |
| `CB_IMAGE_REPO` | yes | Repo path, e.g. `<namespace>/cb-probe` |
| `CB_REGISTRY_USER` / `CB_REGISTRY_PASSWORD` | yes | Push credentials (never hardcode) |

```bash
export CB_REGISTRY=ghcr.io CB_IMAGE_REPO=clousight/cb-probe
export CB_REGISTRY_USER=... CB_REGISTRY_PASSWORD=...
./deploy/cb-probe/build-push.sh
```

Then set the pull reference (`registry/<repo>:<tag>`) as the carrier's base
image — csbench target `eci_image` or terraform `var.eci_image`:

```yaml
probe:
  eci_image: ghcr.io/clousight/cb-probe:<sha>
```

For a private Aliyun-internal pull, mirror the image into ACR and use the
`registry-vpc.<region>.aliyuncs.com/...` reference — that is the only Aliyun-
specific optimization, and it is entirely optional.
