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
manifests. In 0.2.0 these inputs are trusted local configuration:

- run only workloads and manifests you have reviewed;
- remote assets can make outbound requests;
- cloud credentials remain in provider SDK/default credential chains and must
  not be embedded in RunSpec or result files;
- `skeleton` adapters are not runnable.

Workload sandboxing, protocol limits and stricter path/URI validation are part
of the approved Phase 1D hardening work. Until then, do not run untrusted
third-party workload packages.
