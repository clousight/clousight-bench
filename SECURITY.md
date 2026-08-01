# Security

## Reporting

Clousight Bench is a public repository. **Do not open a public issue, pull
request or discussion for a suspected vulnerability.** Report it privately
through GitHub Security Advisories:

<https://github.com/clousight/clousight-bench/security/advisories/new>

We acknowledge reports within five working days. Commercial plugins live in a
separate private repository; a vulnerability that only affects a commercial
plugin should be reported through the same advisory form and will be routed
privately.

## Current trust boundary

Clousight Bench runs workload executables and reads asset URLs declared by
manifests. As of the sandbox slice (layers 1+2), those inputs run under three
enforced constraints:

- **Path boundary** — a workload's `artifact` paths and a manifest's bundled
  asset paths are resolved inside the workload directory; `..` escapes, absolute
  paths and symlink escapes are rejected (`SandboxViolation`).
- **Resource limits** — the workload subprocess runs under POSIX CPU / memory /
  file-size / open-file limits (generous defaults, tunable via `target.limits`).
  On non-POSIX platforms a limit degrades to a no-op with a warning.
- **Asset URIs** — remote assets must be `https`, cannot target
  loopback / link-local / private / cloud-metadata hosts (SSRF guard), and can
  be further restricted with `target.asset_allow_hosts`.

What is **not** yet provided: filesystem, network and process isolation
(sandbox layers 3-5). A determined adversary running arbitrary code inside a
workload is not strongly contained — review workloads and manifests you do not
trust before running them. The constraints above close the largest incidental
exploitation surface (arbitrary file read, runaway processes, SSRF), not a
sandbox against hostile code.

Other boundary notes, unchanged:

- cloud credentials remain in provider SDK / default credential chains and must
  not be embedded in RunSpec or result files;
- `skeleton` adapters are not runnable.

## Result Verification

Every result file written by `csbench` carries a `fingerprints.record_digest`
field that can be independently verified. This section documents how the hash
is computed so third parties can reproduce it without the `csbench` tool.

### Canonical JSON encoding

All digests (the three fingerprints and `record_digest`) use the same
canonical JSON encoding:

- Object keys are sorted **lexicographically** (recursive).
- No insignificant whitespace — `separators=(",", ":")`.
- Strings are UTF-8 encoded.
- Non-finite floats (`NaN`, `Infinity`, `-Infinity`) are **rejected** —
  they never appear in result files.

### `record_digest` computation

1. Deep-copy the entire result payload (a Python `dict`).
2. Remove `fingerprints.record_digest` from the copy.
3. Serialise the copy with the canonical JSON encoding above.
4. SHA-256 the resulting UTF-8 bytes.
5. The digest is the string `"sha256:"` followed by the lower-case hex digest.

### Self-contained verification example

```python
import copy, hashlib, json, sys
from pathlib import Path

def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)

def verify_record(path: str) -> bool:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    body = copy.deepcopy(payload)
    body.get("fingerprints", {}).pop("record_digest", None)
    blob = canonical_json(body).encode("utf-8")
    computed = "sha256:" + hashlib.sha256(blob).hexdigest()
    stored = payload.get("fingerprints", {}).get("record_digest", "")
    ok = stored == computed
    print("ok" if ok else f"MISMATCH\n  stored:   {stored}\n  computed: {computed}")
    return ok

if __name__ == "__main__":
    all_ok = all(verify_record(p) for p in sys.argv[1:])
    sys.exit(0 if all_ok else 1)
```

Or use the built-in command: `csbench verify [results-dir]`.

### Three fingerprints

Each result also carries three coarser fingerprints:

| Fingerprint | Covers |
|-------------|--------|
| `benchmark` | task_id, task_revision, scorer_revision, workload, assets, params |
| `environment` | region, execution mode, os, python version, environment facts |
| `implementation` | core_version, domain, adapter, plugin_versions |

These use the same canonical encoding and enable comparability checks — two
runs with identical `benchmark` and `environment` fingerprints measured the
same thing in the same conditions and can be statistically aggregated.
