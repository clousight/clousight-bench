# Task 5 Report — Trusted Result Fingerprints

## Status

Completed in scope. Added the three deterministic fingerprints and the
persisted-record digest without changing the orchestrator.

## Implementation

- Added `UNKNOWN = "unknown"` for migration-time unknown fingerprints.
- Added benchmark, environment and implementation fingerprints using the
  shared canonical JSON and full SHA-256 digest format.
- Canonicalized asset ordering independently of caller order.
- Redacted secret-keyed values and exact current username/hostname/FQDN values
  before hashing, including fixed fingerprint fields and free-form maps.
- Added record digest generation that deep-copies the payload and removes
  `fingerprints.record_digest` before hashing.
- Exercised `ResultRecord`, `Fingerprints`, `Identity`, `Environment`, and
  `RunInfo` payloads in the digest tests.

## TDD Evidence

1. Initial focused run failed during collection with the expected
   `ModuleNotFoundError: clousight_bench.core.fingerprints`.
2. After the minimal implementation, the focused suite passed.
3. A self-review test for machine identity in fixed fields failed against the
   first implementation, then passed after sanitizing complete fingerprint
   inputs.

## Verification

- Focused: `uv run pytest tests/test_fingerprints.py -v` — 13 passed.
- Full suite: `uv run pytest -q` — 147 passed, 1 skipped.
- Lint: `uv run ruff check src tests` — all checks passed.

## Self-review

- No orchestrator, persistence, report, or legacy schema integration was
  introduced ahead of its scheduled task.
- Fingerprints are meaning-sensitive for all controlled inputs tested and use
  untruncated 64-hex-character SHA-256 values.
- Input payloads are not mutated.

## Concerns

None blocking. Identity exclusion intentionally replaces exact matches only,
consistent with `redaction.find_identity_leaks`; substrings are retained.
