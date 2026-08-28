# Multi-cloud debt cleanup — round 3 (final)

Closes out the remaining OSS-named-key debt deferred by rounds 1–2. These are
migration/policy changes (user config schema + a cross-process wire field), not the
clean internal refactors of the earlier rounds — hence the care below. User chose the
most thorough scope; config-key renames use a **clean break + fail-loud** on legacy keys
(consistent with the codebase's no-compat-alias / fail-loud philosophy), and the wire
field uses a **dual-read migration** (the one place back-compat is load-bearing, because a
separately-installed in-region probe deserializes it).

Naming target: unify on the round-1 `blob_*` vocabulary — `oss_bucket`→`blob_bucket`,
`probe_oss_prefix`→`probe_blob_prefix`, `JobSpec.oss_prefix`→`blob_prefix`.

## Global Constraints

- **Quality gates (every task, before commit) — run with `--no-sync`** to keep the pinned
  venv: `uv run --no-sync ruff check src tests`, `uv run --no-sync mypy src` (must say
  "Success ... 122 source files"; if you instead see a numpy `.pyi` "Type statement" error
  the venv drifted — `uv pip install "numpy<2.5" --quiet` once then re-run, or ignore if
  numpy is simply absent), `uv run --no-sync pytest -q` (baseline **1224 passed, 1 skipped**;
  1223–1224 passed / 1–2 skipped, zero failures = green). `tests/test_layering.py` stays green.
- **No compat aliases for the config/internal keys** (clean break). The ONE exception is the
  `JobSpec` wire field, where `from_dict` MUST dual-read old+new (Task 2) — that is a
  deliberate migration shim, not an alias, and it is temporary.
- **Docs in the same change:** update every affected example config + runbook prose
  (`configs/swe-bench-smoke.plan.yaml`, `docs/swe-bench-live-runbook.mdx` + zh,
  `docs/probe-carrier.mdx` + zh, `docs/runbooks/prod-smoke.md`). **Do NOT edit dated files
  under `docs/plans/` or `docs/specs/`** — immutable history. Run
  `uv run --no-sync python scripts/gen_docs.py` and commit any drift.
- **Do not commit `uv.lock`** churn (`git checkout -- uv.lock` if touched). Do NOT commit
  `docs/plans/2026-08-28-multicloud-debt-round3.md` inside a task — the controller commits
  the plan separately.
- **Commit hygiene:** stage explicit paths only (never `-A`/`.`), verify
  `git diff --cached --stat`, conventional commit messages matching `git log --oneline -10`.

## Task 1: Rename the OSS-named config & internal keys (clean break + fail-loud)

**Scope:** two keys that are provider-agnostic but OSS-named. Neither is the `JobSpec` wire
field (that's Task 2).

- **`oss_bucket`** — a USER-FACING YAML `target:` key AND an internal control-plane
  prov_spec dict key. The user-facing smell is real: AWS users currently write `oss_bucket`
  to name an **S3** bucket (`aws/campaign_probe.py:33,73` read it; `test_aws_campaign_probe.py`
  asserts it).
- **`probe_oss_prefix`** — NOT a user key; it is produced by the `CampaignProbeHook` return
  (`campaign_probe_base.py:87`), documented at `core/plugin.py:348`, and read back inside the
  control plane by `aliyun/transport.py:1006` and `aws/transport.py:1037`. Internal only.

**Changes:**

1. Rename every `oss_bucket` occurrence → `blob_bucket` across src (user-YAML reads AND
   internal prov_spec dict keys, so nothing is left half-named): `cli.py:915,962`;
   `campaign_probe_base.py:72`; `aliyun/provider.py:58,102`; `aws/campaign_probe.py:33,73`;
   `aliyun/transport.py:80,101,183,985,1654,1794` (both the `target.get("oss_bucket")` reads
   and the internal `{"oss_bucket": ...}` / `spec.get("oss_bucket")` dict keys — keep the
   internal producer/consumer in lockstep).
2. Rename every `probe_oss_prefix` → `probe_blob_prefix`: `campaign_probe_base.py:64(docstring),87`;
   `aliyun/transport.py:1006`; `aws/transport.py:1037`; `core/plugin.py:348` (docstring example).
   (Leave the `oss_prefix=` KEYWORD at transport.py:1006/1037 alone here — that's the JobSpec
   field, renamed in Task 2. Only the `.get("probe_oss_prefix")` lookup key changes in Task 1.)
3. **Fail-loud on the legacy user key.** Add a small helper (e.g. `_target_from_cfg(cfg)` in
   `cli.py`, or extend the existing extraction) that pulls `cfg.get("target", {})` AND raises
   `UserInputError` (from `core/errors.py`) with an actionable message
   ("`oss_bucket` was renamed to `blob_bucket`; update your target config") if the legacy
   `oss_bucket` key is present. Apply it at the target-extraction sites: `cli.py:155,350`,
   the plan-merge path `cli.py:666-669`, and `_prod_target` `cli.py:903-909`. Do NOT silently
   accept the old key. (Only `oss_bucket` needs the guard — `probe_oss_prefix` is internal.)
4. Update CLI help text `cli.py:1150,1165,1168` ("needs oss_bucket + region" → "blob_bucket").
5. Update example config + docs prose: `configs/swe-bench-smoke.plan.yaml`,
   `docs/swe-bench-live-runbook.mdx` (+ `docs/zh/…`), `docs/probe-carrier.mdx` (+ zh),
   `docs/runbooks/prod-smoke.md`. (NOT dated docs/plans/.)
6. Update all affected tests (they assert on these keys): `test_prod_submit.py`,
   `test_aliyun_remote_probe_client.py`, `test_probe_sink_wired.py`,
   `test_campaign_carrier_lifecycle.py`, `test_artifact.py`, `test_aliyun_campaign_hook.py`,
   `test_aliyun_campaign_default_carrier.py`, `test_aws_campaign_probe.py`. Add ONE test
   asserting the fail-loud path: a config with legacy `oss_bucket` raises `UserInputError`
   with the rename hint.

**Verify:** gates; `grep -rn "oss_bucket\|probe_oss_prefix" src` returns nothing (dated
docs excluded); the new fail-loud test passes; `grep -rn "oss_bucket" configs docs` clean
except dated `docs/plans/`.

## Task 2: Migrate the `JobSpec.oss_prefix` wire field → `blob_prefix` (dual-read)

**Problem:** `JobSpec.oss_prefix` (`probe/jobs.py:53`) is a SERIALIZED cross-process field.
`to_dict()` (:63) emits the literal key `"oss_prefix"`; `from_dict()` (:81) reads it; the
control plane PUTs the job blob (`blob_channel.py`) and a SEPARATELY-INSTALLED in-region
probe wheel reads it back (`agent_loop.py`). The probe wheel is version-pinned to the
control plane (`campaign_probe_base._published_code_spec()`), so the skew window is narrow —
but a job blob written before an upgrade, or left from a prior run, can still be read after,
so back-compat on READ is load-bearing.

**Changes:**

1. Rename the dataclass field `JobSpec.oss_prefix` → `blob_prefix` (`probe/jobs.py:53`).
2. `to_dict()` (`jobs.py:63`) emits the NEW key `"blob_prefix"` only.
3. `from_dict()` (`jobs.py:81`) **DUAL-READS**: prefer `"blob_prefix"`, fall back to legacy
   `"oss_prefix"` if the new key is absent — e.g.
   `blob_prefix=str(d.get("blob_prefix", d.get("oss_prefix", "")))`. Add a comment marking
   this as a temporary read-migration shim (safe to drop once no pre-migration job blobs
   can exist). This is the ONLY back-compat allowance in round 3.
4. Update the two producer sites that set the kwarg (they build `JobSpec(...)`):
   `aliyun/transport.py:1006` and `aws/transport.py:1037` — change the keyword
   `oss_prefix=` → `blob_prefix=`. (Their VALUE source is `target.get("probe_blob_prefix")`
   after Task 1 — confirm Task 1 already renamed that lookup key; if this task runs on a tree
   where Task 1 landed, the `.get` is already `probe_blob_prefix`.)
5. Update tests: `test_probe_jobs.py` (the round-trip test — assert `to_dict` emits
   `blob_prefix`, and ADD a dual-read test: `from_dict({"oss_prefix": "x", ...})` still
   populates `blob_prefix="x"`), plus any other test constructing `JobSpec(oss_prefix=...)`
   or asserting the serialized key.

**Verify:** gates; `grep -rn "oss_prefix" src` returns nothing EXCEPT the single legacy-key
fallback string in `jobs.py:from_dict` (the migration shim); the round-trip and dual-read
tests pass.

## Task 3: Cosmetic — simplify the `controller_reaper_spec` hook resolution

**Problem (from round-2 reviews):** `core/controller_main.py` `build_reaper` resolves the
hook via `fn = getattr(plugin, "controller_reaper_spec", None); spec = fn(region, log) if
callable(fn) else None`. The method is a concrete member of the `RuntimeProviderPlugin` ABC
(always present, defaults to `None`), so the `getattr`/`callable` guard is dead defensiveness
and diverges from the peer resolver `prod_submit.py` which calls `plugin.controller_tf_spec()`
directly.

**Changes:**

1. Simplify to a direct call mirroring `prod_submit`'s pattern, e.g.
   `spec = plugin.controller_reaper_spec(region, log) if plugin is not None else None`.
   Preserve the exact same behavior (spec `None` → deleters fall back to `_noop_del`; the
   `iid = "" if spec is None` coupling stays valid). Optionally add the one-line comment the
   round-2 review suggested noting that an empty instance-id only ever reaches a no-op.
2. No behavior change; the existing `build_reaper` tests must pass unchanged.

**Verify:** gates; `grep -n "getattr(plugin" src/clousight_bench/core/controller_main.py`
clean; existing controller_main reaper tests green.
