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
