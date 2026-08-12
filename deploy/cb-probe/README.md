# cb-probe container image

The `cb-probe` image runs the ECI-side OSS poller for the in-region private
probe path. It contains only `python:3.12-slim` + the `clousight_bench` package
installed with the `probe` extra (`requests`, `oss2`) — no LangChain, no OTel,
no heavy agent bundle.

The container's entrypoint is:

```
python -m clousight_bench.domains.agent_runtime.probe.agent_loop
```

which reads the following environment variables at runtime:

| Variable | Required | Description |
|---|---|---|
| `CB_PROBE_BUCKET` | yes | OSS bucket name |
| `CB_PROBE_REGION` | yes | Aliyun region ID, e.g. `cn-hangzhou` |
| `CB_PROBE_CONTROL_PREFIX` | yes | Per-campaign control prefix (campaign ID) |
| `CB_PROBE_TOKEN` | no | Optional bearer token (parity with HTTP probe) |

The ECI task sets these from the `CB_PROBE_*` environment variables injected by
the csbench run-plan hook (`start_campaign_probe`).

## Building and pushing

Set the following environment variables (obtain from terraform outputs or the
ACR console — **never hardcode credentials**):

| Variable | Required | Description |
|---|---|---|
| `CB_ACR_NAMESPACE` | yes | ACR namespace (e.g. `clousight`) |
| `CB_ACR_USER` | yes | ACR username |
| `CB_ACR_PASSWORD` | yes | ACR password or access token |
| `CB_ACR_REGION` | no | Aliyun region (default: `cn-hangzhou`) |

From the **repo root** (or any directory — the script resolves the root via git):

```bash
export CB_ACR_NAMESPACE=clousight
export CB_ACR_USER=<acr-username>
export CB_ACR_PASSWORD=<acr-password>

./deploy/cb-probe/build-push.sh
```

The script:
1. Resolves the git short SHA as the image tag.
2. Builds the image using `deploy/cb-probe/Dockerfile` with the repo root as
   the build context.
3. Logs in to `registry.cn-hangzhou.aliyuncs.com` (public endpoint).
4. Pushes the image.
5. Prints the **VPC-internal** image reference at the end, e.g.:
   ```
   registry-vpc.cn-hangzhou.aliyuncs.com/clousight/cb-probe:<sha>
   ```

## Using the image reference

The printed `registry-vpc` reference is the one ECI instances pull from inside
the VPC (no public internet egress needed). Use it as:

- The terraform `eci_image` variable in the ECI task definition.
- The csbench target `eci_image` in the run-plan yaml:
  ```yaml
  probe:
    eci_image: registry-vpc.cn-hangzhou.aliyuncs.com/clousight/cb-probe:<sha>
  ```

The ACR namespace and repository (`cb-probe`) are provisioned by the terraform
module in `deploy/terraform/` (sibling task). The public push endpoint and the
VPC-internal pull endpoint refer to the same image; only the hostname differs.
