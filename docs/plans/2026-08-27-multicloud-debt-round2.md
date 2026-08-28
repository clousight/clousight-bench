# Multi-cloud debt cleanup — round 2

Follow-up to the 2026-08-26 cleanup (merged 8c4f42a..2392bf3). The round-1 final review
and a 2026-08-27 verification pass surfaced these residual items. This plan takes only the
SAFE, self-contained ones. Risky wire-format / config-schema renames are explicitly
DEFERRED below (they need migration machinery, not a cleanup rider).

The centerpiece is Task 3: it removes the LAST alibabacloud SDK code from `core/`, by
applying the exact registry-hook pattern that round-1 Task 2 (ec50ef5) used for terraform.

## Global Constraints

- **Quality gates (every task, before commit):** `uv run ruff check src tests`,
  `uv run mypy src`, `uv run pytest -q` (default markers exclude live/slow) must all pass.
  Baseline on this branch (off main 2392bf3): **1220 passed, 1 skipped** (a 2nd skip is
  env-gated; treat 1219–1220 passed / 1–2 skipped as green, zero failures).
- **Layering guard:** `tests/test_layering.py` must keep passing — no NEW module-level
  imports of `domains`/`suites`/`viewer` from `core/`. Task 3 must REDUCE core's provider
  coupling, not add to it.
- **No backwards-compat aliases** (the pro repo has zero usages of these symbols).
- **Docs in the same change:** update affected prose in the same commit. `scripts/gen_docs.py`
  only regenerates the task-inventory block in `architecture.mdx`; `probe-carrier.mdx` is
  hand-maintained — edit it directly. Run `uv run python scripts/gen_docs.py` and commit any
  drift regardless.
- **Do not commit `uv.lock`** churn — `git checkout -- uv.lock` if `uv run`/`uv sync`
  rewrites it (known local uv-version drift). Use `uv sync --all-extras --frozen` after
  editing `pyproject.toml` entry points.
- **Commit hygiene:** stage explicit paths only (`git add <path> ...`, never `-A`/`.`),
  verify `git diff --cached --stat` before committing, `git mv` for moves. Conventional
  commit messages matching `git log --oneline -10`. Do NOT commit
  `docs/plans/2026-08-27-multicloud-debt-round2.md` inside a task — the controller commits
  the plan file separately.

## Task 1: Fix the `CarrierError` import direction

**Problem (corrected from the debt note):** `CarrierError` is already defined in the
neutral `domains/agent_runtime/carrier_base.py:22`; `aliyun/ecs_carrier.py:9` merely
re-exports it. The only wrong-direction edge is that the provider-agnostic
`domains/agent_runtime/dev_wheel.py:24` imports `CarrierError` *through* the aliyun
re-export (shared → provider), and its test mirrors that.

**Changes:**

1. `src/clousight_bench/domains/agent_runtime/dev_wheel.py:24` — import `CarrierError` from
   `clousight_bench.domains.agent_runtime.carrier_base` instead of `...aliyun.ecs_carrier`.
   (Confirm dev_wheel doesn't also pull other aliyun-only symbols from that same import line;
   if it does, split so only genuinely-aliyun symbols come from the aliyun module. Per the
   verification, only `CarrierError` is involved — check and confirm.)
2. `tests/test_dev_wheel.py:108` — repoint the same import to `carrier_base`.
3. Leave `aliyun/ecs_carrier.py`'s re-export of `CarrierError` in place ONLY if something
   still relies on it; grep first — if nothing imports `CarrierError` from `ecs_carrier`
   after step 1/2, drop it from `ecs_carrier.py`'s `__all__` and its import line to keep the
   re-export surface honest (no compat leftovers). If `aws/carrier.py` or aliyun-internal
   code needs it, it already imports from `carrier_base` — verify.

**Verify:** quality gates; `grep -rn "from clousight_bench.domains.agent_runtime.aliyun.ecs_carrier import" src tests`
shows no neutral module pulling `CarrierError` through aliyun.

## Task 2: Documentation & comment-only de-vendoring (zero behavior change)

**Problem:** three comment/docstring-level leaks, none touching identifiers, wire formats,
or APIs.

**Changes:**

1. **Factual doc error** — `docs/probe-carrier.mdx:24` says the probe modules are "all in
   the core package, under `probe/`". They live in `domains/agent_runtime/probe/`. Fix the
   wording (e.g. "all under `domains/agent_runtime/probe/`"). Apply the identical fix to the
   Chinese mirror `docs/zh/probe-carrier.mdx:17` ("均位于核心包 `probe/` 目录下" → "均位于
   `domains/agent_runtime/probe/` 目录下"). Both are hand-maintained; gen_docs won't touch them.
2. **ECI vocabulary in generic probe modules** — in the provider-agnostic modules only
   (`probe/blob_channel.py`, `probe/agent_loop.py`, `probe/jobs.py`,
   `probe/blob_dispatch_client.py`), "ECI" (Aliyun Elastic Container Instance) is used in
   comments/docstrings to mean "the in-region probe" even though the mechanism now also
   serves AWS EC2. Replace generic-mechanism "ECI" usages with cloud-neutral wording
   ("in-region probe" / "the probe side" / "probe host"). DO NOT touch genuinely-Aliyun ECI
   references in `probe/oss_client.py` and `aliyun/ecs_carrier.py` (those are correctly
   scoped). Watch for the `agent_loop.py:190` "ECI container" phrase — it's also stale (runs
   on a stock instance, not a container); neutral wording fixes both.
3. **`chunks_to_artifacts` seam clarity** — `probe/blob_sync.py:32` `chunks_to_artifacts`
   has no production caller; it's the unwired final step of an otherwise-wired path
   (`BlobChunkSink.close()` writes the manifest → `runner.py:70` currently extracts only
   `chunk_refs`, not full artifact records). Do NOT wire in new behavior. Add a short comment
   at `blob_sync.py:32` (and a one-line pointer at `runner.py:70` where `chunk_refs` is
   built) naming the intended consumer, so the function reads as a deliberate seam, not dead
   code. Keep the existing tests (`tests/test_probe_blob_artifacts.py`) as the behavioral
   guard.

**Verify:** quality gates; `grep -rn "ECI" src/clousight_bench/domains/agent_runtime/probe/`
returns only genuinely-Aliyun hits in `oss_client.py` (none in the generic modules);
`grep -rn "core package" docs/` clean.

## Task 3: Move the prod-controller reaper's Aliyun SDK code out of `core/` behind a provider hook

**Problem:** `core/controller_main.py` still contains three factory functions with lazy
alibabacloud SDK bodies — `_live_delete_runtime` (:92, AgentRun delete), `_live_delete_nat`
(:111, VPC NAT/EIP teardown with Aliyun ordering + endpoints), `_live_delete_self` (:191,
ECS delete) — plus the Aliyun resource-name constants `_NAT_NAME`/`_NAT_EIP_NAME` (:38-39)
and the Aliyun metadata host (:40). These are the same category of vendor knowledge round-1
Task 2 evicted for terraform, only on the runtime side. They pass the layering test solely
via the composition-root lazy-import exemption; the goal is to shrink that exemption to
zero alibabacloud imports.

**Precedent to mirror exactly (round-1 Task 2, ec50ef5):**
`ControllerTfSpec` frozen dataclass + `RuntimeProviderPlugin.controller_tf_spec()` hook
(default `None`) in `core/plugin.py`; `AliyunRuntimeProvider.controller_tf_spec()` returns
the alicloud data verbatim (`aliyun/provider.py`); `core/prod_submit.py` resolves via
`get_runtime_provider(provider)` and fails loud when absent. Read all three before starting.

**Changes:**

1. In `core/plugin.py`, add a frozen dataclass carrying the three delete callables, e.g.
   `ControllerReaperSpec(delete_runtime: Callable[[str], None], delete_nat: Callable[[], None],
   delete_self: Callable[[str], None])`, and an optional hook
   `RuntimeProviderPlugin.controller_reaper_spec(region: str, log: Callable[[str], None]) ->
   ControllerReaperSpec | None` defaulting to `None`. Docstring: "Live delete callables for
   this provider's prod-controller reaper; None = the provider has no wired prod-controller
   reaper." Mirror the existing `controller_tf_spec` idiom (placement, naming, docstring tone).
2. Move the three `_live_delete_*` bodies AND the `_NAT_NAME`/`_NAT_EIP_NAME` constants and
   the metadata-host constant out of `core/controller_main.py` into the Aliyun provider pack
   (follow the pack's structure — a new module like `aliyun/controller_reaper_live.py`, or
   alongside `aliyun/ecs_carrier.py` which already wraps these exact SDKs via `Ecs20140526Sdk`;
   the implementer picks the natural home). Implement `AliyunRuntimeProvider.controller_reaper_spec()`
   to build and return the `ControllerReaperSpec`. Behavior must be byte-identical: same SDK
   calls, same VPC ordering (unassociate EIP → delete NAT → settle → release EIP), same
   endpoints, same resource names.
3. In `core/controller_main.py`, `build_reaper` resolves the spec via
   `get_runtime_provider(infer_provider(platform))` (platform from the `CB_PLATFORM` env it
   already reads) and uses the spec's callables as the defaults where `_live_delete_*` were
   used — KEEPING the existing injection seam so `tests/test_controller_main.py`'s injected
   fakes still win. If no provider / no hook / spec is `None`, degrade to the current
   best-effort behavior (a no-op reaper that logs), consistent with `main()`'s existing
   try/except — do NOT introduce a hard failure that the open-core (no runtime provider)
   path can't survive. After this, `core/controller_main.py` must contain zero
   `alibabacloud*` imports (module-level or lazy).
4. **`CB_PLATFORM` hardening (same file, same spirit):** `controller_main.py:253` defaults
   `CB_PLATFORM` to `"aliyun-agentrun"` — the last implicit-Aliyun default on this path. The
   Aliyun carrier user-data always sets it (`aliyun/ecs_carrier.py:55`), so the default is
   only reached when misconfigured. Decide and implement ONE of: (a) keep the default but
   add a clear comment that it's the Aliyun-carrier fallback, or (b) make a missing
   `CB_PLATFORM` fail loud with an actionable message. Prefer (b) for consistency with
   Task 2's fail-loud philosophy ONLY if no test/build path relies on the default; grep
   `tests/test_controller*.py` for `CB_PLATFORM` first and update/confirm. If (b) ripples
   into tests that construct `build()` without the env, fall back to (a) with the comment and
   say so in your report. This sub-item must not expand Task 3's blast radius — if in doubt,
   choose (a).
5. Tests: add a hook test mirroring `test_prod_submit.py`'s `controller_tf_spec` tests — the
   Aliyun provider returns a `ControllerReaperSpec` whose callables are wired (assert the
   spec is non-None and carries three callables; the live SDK bodies stay `# pragma: no
   cover`). Keep/adjust `tests/test_controller_main.py::test_build_reaper_wires_deleters_in_order`
   and `test_build_reaper_defaults_live_runtimes_to_ledger` — they inject fakes and must
   still pass; if `build_reaper`'s signature/resolution changed, update them minimally to
   match without weakening their assertions.

**Verify:** quality gates; `grep -rn "alibabacloud" src/clousight_bench/core/` returns
empty; `grep -rn "_NAT_NAME\|_NAT_EIP_NAME\|100.100.100.200" src/clousight_bench/core/`
empty; layering test green (and note in the report that core's composition-root exemption
now covers only the neutral `RestrictedReaper` wiring import, no vendor SDK).

## Explicitly DEFERRED (NOT in this plan — do not attempt here)

These need migration machinery, not a cleanup pass; each deserves its own plan:

- **`JobSpec.oss_prefix` rename** — it is a SERIALIZED cross-process wire field. `JobSpec.to_dict()`
  (`probe/jobs.py:63`) / `from_dict()` (:81) emit/read the literal `"oss_prefix"` key; the
  control plane PUTs the job blob (`blob_channel.py:98`) and a separately-installed in-region
  probe wheel reads it back (`agent_loop.py:84`). Renaming needs dual-read (accept old+new
  keys) coordinated with the pinned probe wheel version — a wire-format migration.
- **`oss_bucket` / `probe_oss_prefix` config keys** — user-facing YAML config schema AND the
  `CampaignProbeHook` return contract (`core/plugin.py:318`), used by BOTH aliyun and aws
  paths. Renaming breaks existing user configs; needs config-key aliasing (accept both) plus
  lockstep updates to the `probe_oss_prefix → JobSpec.oss_prefix` copy at
  `aliyun/transport.py:1006` and `aws/transport.py:1037`.
