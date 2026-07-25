# Security

## Reporting

This repository remains private during the 0.2 developer-preview phase.
Report suspected vulnerabilities through the private repository's maintainer
channel; do not copy vulnerability details into public issues or chat rooms.

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
