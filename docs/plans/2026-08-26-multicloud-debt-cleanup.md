# Multi-cloud debt cleanup

Five debts surfaced by the 2026-08-26 architecture audit. All are vendor-neutrality /
plugin-consistency fixes; none change benchmark behavior.

## Global Constraints

- **Quality gates (every task, before commit):** `uv run ruff check src tests`,
  `uv run mypy src`, `uv run pytest -q` (default markers exclude live/slow) must all pass.
- **Layering guard:** `tests/test_layering.py` must keep passing — no new module-level
  imports of `domains`/`suites`/`viewer` from `core/`. Cross-layer wiring goes through
  entry points (`core/registry.py`) or lazy function-body imports at the composition root.
- **Docs in the same change:** grep `docs/`, `README.md`, `AGENTS.md`, `CONTRIBUTING.md`
  for any symbol/path you rename or move and update the prose in the same commit. Run
  `uv run python scripts/gen_docs.py` afterwards and commit any regenerated drift.
- **No backwards-compat aliases.** The companion pro repo has zero usages of the renamed
  symbols (verified 2026-08-26). Rename cleanly; do not leave `OssClient = BlobStore`
  style shims.
- **Commit hygiene:** stage explicit paths only (`git add <path> ...`, never `git add -A`
  or `git add .`), then verify `git diff --cached --stat` shows exactly the intended files
  before committing. Commit messages follow the repo's conventional style, e.g.
  `refactor(core): ...` — look at `git log --oneline -10` for tone.
- **After editing `pyproject.toml` entry points**, run `uv sync --all-extras` so the
  editable install's entry-point metadata is refreshed before running tests.

## Task 1: Rename the core blob-store abstraction `OssClient` → `BlobStore`

**Problem:** `core/blobstore.py` names the vendor-neutral 4-method blob-store ABC
`OssClient` (an Aliyun term), forcing AWS's `S3Client` to subclass a class named after a
competitor's product and to carry a "mirror the Aliyun OSS pair" apology in its header.
The leak spreads through `core/campaign_channel.py`, `core/controller_main.py`,
`domains/agent_runtime/campaign_probe_base.py`, `carrier_base.py`, `session_memory.py`,
`dev_wheel.py`, and the whole `probe/` package.

**Changes:**

1. In `src/clousight_bench/core/blobstore.py`: rename class `OssClient` → `BlobStore` and
   `InMemoryOssClient` → `InMemoryBlobStore`. Update the module docstring (it references
   the implementing files by path — keep those references accurate after Task 1's file
   renames below).
2. Update every import site / type hint / isinstance / docstring that refers to the
   abstraction, across `src/` and `tests/`. `grep -rn "OssClient" src tests` must come
   back empty EXCEPT for genuinely-Aliyun concrete classes:
   - `probe/oss_client.py` keeps its file name and its concrete classes `Oss2Client` and
     `EcsRamRoleOssClient` (they really are OSS clients), but its imports/`__all__` switch
     to `BlobStore`/`InMemoryBlobStore`. Do NOT re-export the ABC from this module if the
     only reason was the old shared name — keep re-exports only if existing importers
     rely on them (check first; if `probe/oss_client.py` re-exports are used by the probe
     wheel's minimal install path, keep them but under the new names).
   - `probe/s3_client.py` keeps its file name and `S3Client`/`Ec2MetadataS3Client` class
     names, subclassing `BlobStore`; delete the "mirror the Aliyun OSS pair" apology
     header and replace with a neutral one ("AWS S3 implementations of the core
     blob-store interface", mirroring oss_client.py's new header).
3. Rename the provider-agnostic probe modules whose names leak OSS but whose code is
   generic over the ABC (they are reused verbatim by the AWS probe path):
   - `probe/oss_channel.py` → `probe/blob_channel.py`
   - `probe/oss_sync.py` → `probe/blob_sync.py`
   - `probe/oss_sink.py` → `probe/blob_sink.py`
   - `probe/oss_dispatch_client.py` → `probe/blob_dispatch_client.py`
   Use `git mv`. Update all importers (`aliyun/transport.py`, `aws/transport.py`,
   `campaign_probe_base.py`, `carrier_base.py`, `probe/agent_loop.py`,
   `probe/dataplane.py`, `probe/runner.py`, `core/controller_main.py`,
   `core/campaign_channel.py`, plus tests). Rename the matching test files
   (`tests/test_probe_oss_channel.py` → `tests/test_probe_blob_channel.py`, likewise for
   sink/sync/dispatch_client — only where the module under test was renamed; leave
   `test_probe_oss_artifacts.py` etc. alone if they test genuinely-OSS behavior — read
   them first and decide per file).
4. Sweep generic docstrings/comments in the renamed modules: where they describe the
   mechanism generically, say "blob store"; where they describe the Aliyun deployment
   specifically (e.g. ECI + OSS key layout examples), keeping "OSS" is correct.
   Variable/parameter names like `oss` / `oss_client` in the generic modules and core
   become `store` / `blob_store`; in Aliyun-specific modules they may stay.

**Verify:** full quality gates; `grep -rn "OssClient\|InMemoryOssClient" src tests docs`
returns nothing; `grep -rln "oss_channel\|oss_sync\|oss_sink\|oss_dispatch" src tests docs`
returns nothing.

## Task 2: Move the Aliyun-only prod-controller Terraform knowledge out of core

**Problem:** `src/clousight_bench/core/prod_submit.py` hardcodes
`_CONTROLLER_TF_TARGETS` (a list of `alicloud_*` Terraform addresses) and
`_DRIVER_TF_VARS` (plan-yaml driver keys → `controller_*` tf vars of the Aliyun
controller module). The whole prod-controller submit/reap path is Aliyun-only but the
vendor knowledge sits in `core/`.

**Changes:**

1. Define a small frozen dataclass in `core/plugin.py` (next to `RuntimeProviderPlugin`),
   e.g. `ControllerTfSpec(tf_targets: tuple[str, ...], driver_tf_vars: Mapping[str, str])`,
   and add an optional hook on `RuntimeProviderPlugin`:
   `def controller_tf_spec(self) -> ControllerTfSpec | None: return None` — docstring:
   "Terraform surface of this provider's prod-controller profile; None = the provider has
   no wired prod-controller path."
2. Move the two constants verbatim into the Aliyun provider
   (`domains/agent_runtime/aliyun/provider.py` or a sibling module there — follow the
   package's existing structure) and implement `controller_tf_spec()` on
   `AliyunRuntimeProvider`.
3. In `core/prod_submit.py`, resolve the spec at runtime via
   `core.registry.get_runtime_provider(<provider>)` (lazy, function-body import if needed
   to satisfy layering — registry lives in core so a top-level import is fine). Derive
   `<provider>` from whatever the submit path already knows (campaign spec / plan yaml /
   platform string — read the call sites: `cli.py` submit command and
   `core/controller_main.py`). If the resolved provider has no `ControllerTfSpec`, fail
   loudly with an actionable message ("provider 'X' has no prod-controller profile; only
   providers implementing controller_tf_spec() support `csbench submit`") — never fall
   back to the Aliyun targets.
4. Behavior for Aliyun must be byte-identical: same target list, same var mapping, same
   ordering. Add/adjust a test asserting the Aliyun provider's spec equals the previous
   constants, and a test that a provider without the hook produces the loud failure.
5. `grep -n "alicloud" src/clousight_bench/core/` must come back empty after this task
   (check the rest of core too while there — if other `alicloud_*` literals exist in
   core, flag them in your report; fix them only if they belong to this same
   prod-controller path).

**Verify:** full quality gates; the two new tests pass; core grep clean.

## Task 3: Move Aliyun-specific carrier/reaper modules into the `aliyun/` subpackage

**Problem:** layout asymmetry. AWS keeps its carrier/reaper in
`domains/agent_runtime/aws/`; Aliyun's equivalents sit at the domain root
(`ecs_carrier.py`, `reaper.py`, and — verify — `controller_reaper.py`).

**Changes:**

1. `git mv` `domains/agent_runtime/ecs_carrier.py` → `domains/agent_runtime/aliyun/ecs_carrier.py`
   and `domains/agent_runtime/reaper.py` → `domains/agent_runtime/aliyun/reaper.py`.
2. Read `domains/agent_runtime/controller_reaper.py` first: if it is Aliyun-specific
   (ECS/NAT/EIP teardown via alibabacloud SDKs), move it into `aliyun/` too; if it is
   genuinely shared, leave it and say so in your report.
3. Update all importers: `domains/agent_runtime/dev_wheel.py`,
   `domains/agent_runtime/aliyun/_shared.py`, tests
   (`test_ecs_carrier.py`, `test_reaper*.py`, `test_dev_wheel.py`,
   `test_controller_user_data.py`, `test_aliyun_campaign_default_carrier.py`), and any
   others `grep` finds.
4. Update the entry point in `pyproject.toml`:
   `aliyun = "clousight_bench.domains.agent_runtime.aliyun.reaper:AliyunResourceReaper"`.
   Then `uv sync --all-extras` before running tests.
5. No shim modules left behind at the old paths.

**Verify:** full quality gates; `csbench` reaper discovery still lists both reapers
(there is an existing test for reaper registration — find and run it; if none exists,
add a small one that `load_resource_reapers()` yields aliyun+aws).

## Task 4: Version-gate suite/evaluator plugin loading

**Problem:** `core/registry.py`'s `load_benchmark_suites()` / `load_evaluators()`
(bottom of the file) skip the `_check_api_version(ep, inst)` gate that every other loader
(domains, enrichers, reapers, runtime providers, span exporters, asset resolvers)
applies. Third-party suites/evaluators bypass the plugin-API version contract.

**Changes:**

1. Call `_check_api_version(ep, inst)` in both loaders, at the same point in the loop as
   the sibling loaders do (after instantiation, before dedup/type checks — match the
   existing pattern exactly).
2. Add tests mirroring the existing version-gate tests (find them:
   `grep -rn "requires_plugin_api" tests/`): a suite/evaluator with an incompatible
   `requires_plugin_api` range is rejected with the same error type the other loaders
   raise; one with a compatible range loads.
3. If `BenchmarkSuite`/`Evaluator` ABCs document their contract in docstrings, mention
   the optional `requires_plugin_api` attribute the same way sibling plugin ABCs do
   (check `core/plugin.py` for precedent; keep parity, don't invent new prose).

**Verify:** full quality gates; new tests pass.

## Task 5: Remove Aliyun-specific copy from `core/cost_notice.py`

**Problem:** the generic live-run cost notice hardcodes Aliyun AgentRun facts: "FC
compute dominates the bill; each fresh AgentRuntime cold-starts ~86s on a cold pool",
"warm FC pool drops it to ~1s". Core prose must be vendor-neutral.

**Changes:**

1. Rewrite the notice text in `core/cost_notice.py` vendor-neutrally while keeping every
   actionable lever: iterate on `--platform local-sim`; single live runs pay a full
   cold start so batch via run-plan/campaign; cap spend with `--cost-budget`. E.g.
   "managed-runtime cold starts often dominate a single run's cost" — no product names
   (FC/AgentRuntime), no vendor-specific numbers (~86s/~1s) in core.
2. Do NOT build a per-provider hint plumbing layer for this — that is over-engineering
   for a console notice. If (and only if) an existing, already-reachable seam can carry
   the Aliyun numbers (e.g. adapter `DOCS`/notes already surfaced near this message),
   move the ~86s fact there; otherwise just drop the numbers and note in your report
   where the fact was preserved or that it was dropped.
3. Update any test asserting the old wording (`grep -rn "86s\|cold pool\|FC compute" tests/`).

**Verify:** full quality gates; `grep -rn "AgentRuntime\|FC \|~86s" src/clousight_bench/core/`
shows no vendor-product references in the notice path.

## Task 6: Rename residual `Oss*` class names in the generic probe modules

**Problem (found by Task 1's review):** the generic modules renamed to `blob_*.py` still
export vendor-named classes — `OssChannel` (blob_channel.py), `OssChunkSink`
(blob_sink.py), `OssProbeClient` (blob_dispatch_client.py) — and `aws/transport.py`
builds `OssChannel(s3, ...)` over S3. Same naming-leak category as Task 1.

**Changes:**

1. Rename `OssChannel` → `BlobChannel`, `OssChunkSink` → `BlobChunkSink`,
   `OssProbeClient` → `BlobProbeClient` (or the closest names matching each class's
   actual role — keep the `Blob` prefix consistent). Update all constructors/importers
   in src and tests. No compat aliases.
2. Sweep the stale vocabulary the review flagged: test function names
   `test_s3_client_implements_oss_client_abc` / `test_ec2_metadata_s3_client_implements_oss_client_abc`
   (they assert against `BlobStore` now), and the `tests/test_blob_dispatch_client.py`
   module docstring opening "the control-plane OSS dispatch client". Rename/reword to
   blob-store vocabulary. Grep for any similar leftovers in the renamed modules' tests.
3. `blob_sync.chunks_to_artifacts` hardcodes `oss://` artifact URIs even on the AWS/S3
   path. Investigate consumers first (grep for `oss://` parsing in src/tests/viewer): if
   the scheme is load-bearing anywhere, parameterize it explicitly (e.g. a `scheme`
   argument supplied by the provider side, aliyun→`oss`, aws→`s3`) and update the
   consumers; if it is purely informational in artifact records, do the same
   parameterization but note that records produced before/after differ only in that
   informational field. Do not invent per-provider plumbing beyond a plain argument.

**Verify:** full quality gates; `grep -rn "OssChannel\|OssChunkSink\|OssProbeClient" src tests`
returns nothing; `grep -rn "oss://" src/clousight_bench/domains/agent_runtime/probe/blob_sync.py`
shows the scheme is no longer hardcoded.
