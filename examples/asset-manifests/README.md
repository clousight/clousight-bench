# Asset manifest templates

Copy-paste templates for the `assets:` block of a workload/task manifest. They
show the three distribution tiers resolved by `core/assets.py`:

| tier | `source` | when |
|------|----------|------|
| bundled | `bundled` | small, license-clean data shipped in the repo (relative path) |
| public  | `remote`  | large PUBLIC datasets — not vendored; declared by `uri + sha256 + license`, downloaded on demand, checksum-verified, cached |
| private | `private` | proprietary datasets / held-out scoring keys — resolved by a licensed `clousight_bench.asset_resolvers` plugin; no plugin → `NeedLicense` |

The engine resolves every declared asset before running and exposes the local
paths to the workload as `params["assets"] = {name: path}`.

## Fill in the checksum (do this once per dataset)

`remote` assets should carry a `sha256` so a run is reproducible and a tampered
or truncated download is rejected. Compute it after a trusted first download:

```bash
curl -L <uri> -o /tmp/asset && shasum -a 256 /tmp/asset
# put the hex digest (no "sha256:" prefix) into the manifest
```

An empty `sha256` is allowed (the download just isn't verified) but discouraged
for anything you publish numbers from.

## License discipline

`remote` requires a non-empty `license` field — this is an auditability gate,
not decoration. Confirm the dataset's terms allow your use (and publishing
derived benchmark numbers) before adding it. The identity folded into
`config_hash` is only `name@version + sha256`; dataset contents never are.

## Templates in this folder

- `bigdata-emr.nyc-taxi.remote.yaml` — public columnar dataset (NYC TLC).
- `agent-runtime.swe-bench-lite.remote.yaml` — public agent task set (HF).
- `retrieval.hotpotqa.remote.yaml` — public multi-hop QA corpus.
- `agent-runtime.held-out-keys.private.yaml` — private held-out scoring keys.
- `bigdata-emr.bundled.example.yaml` — a small bundled asset (baseline).
