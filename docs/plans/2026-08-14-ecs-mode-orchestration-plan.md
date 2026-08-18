# ECS-Mode Orchestration (prod profile) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the run-plan orchestrator onto an ephemeral in-region ECS controller (prod profile); the laptop becomes a thin submit/status/logs/fetch/teardown client communicating purely via OSS polling.

**Architecture:** A `CampaignChannel` layers campaign-level objects on the existing per-campaign `OssChannel` OSS layout. A `CampaignController` (run by the `cb-controller` entrypoint on an ECS instance) polls a launch spec, drives the existing `core/orchestrator.execute` per task serially, writes results/manifest/heartbeat/ledger to OSS, and self-destructs via a restricted delete role. The laptop CLI gains submit/status/logs/fetch/teardown, all OSS-poll based.

**Tech Stack:** Python 3.10+, existing `[store]` extra (duckdb>=1.0, pyarrow>=16), oss2, alibabacloud ECS/AgentRun SDKs, Terraform (aliyun-iam module). No new heavy dependencies.

## Global Constraints

- Run mode named by intent: `mode: dev` (本地开发) / `prod` (生产测评). Never "local"/"ecs" in the config value. dev = existing laptop path (unchanged); prod = this system.
- v1 is SERIAL only (single 二合一 controller does orchestration + probe). No grouped-parallel.
- Comms is PURE OSS POLLING — no long-lived connection; laptop may power off after submit.
- `ResourceLedger` is synced to OSS after every provision/deprovision — cleanup must never depend on the controller or laptop being alive.
- Three-layer resource safety: per-task deprovision → controller self-destruct watchdog → local `teardown` backstop.
- Controller cleanup uses a RESTRICTED delete role (delete this run's runtime + NAT/EIP/SNAT + own ECS only) — never the MAIN account.
- Create path = MAIN-account `terraform apply` once at submit; controller installs `clousight-bench[probe,store]` via cloud-init.
- Reuse: `OssChannel`/`InMemoryOssClient` (`probe/oss_channel.py`, `probe/oss_client.py`), `EcsProbeCarrier` (`ecs_carrier.py`), `core/orchestrator.execute`, `core/store.py` ResultStore (parquet+duckdb), `core/resource_ledger.py` ResourceLedger.
- Code + comments English; user-facing comms Chinese. Frequent commits. TDD.
- OSS layout root: `clousight-bench/control/<campaign_id>/` (matches `OssChannel.prefix`).

---

## File Structure

- Create `src/clousight_bench/core/campaign_spec.py` — `LaunchSpec`, `CampaignManifest`, `TaskEntry` dataclasses + (de)serialization.
- Create `src/clousight_bench/domains/agent_runtime/probe/campaign_channel.py` — `CampaignChannel` (campaign-level OSS objects on top of `OssChannel.prefix`).
- Create `src/clousight_bench/core/controller.py` — `CampaignController` orchestration loop.
- Create `src/clousight_bench/core/watchdog.py` — `SelfDestructWatchdog` (heartbeat + timeout/stop → reap).
- Create `src/clousight_bench/domains/agent_runtime/controller_reaper.py` — `RestrictedReaper` (SDK delete runtime + NAT + self ECS from ledger).
- Create `src/clousight_bench/core/controller_main.py` — `cb-controller` entrypoint.
- Modify `pyproject.toml` — add `cb-controller` console script.
- Modify `src/clousight_bench/cli.py` — add `_cmd_submit/_cmd_status/_cmd_logs/_cmd_fetch/_cmd_teardown` + subparsers.
- Create `src/clousight_bench/core/prod_submit.py` — submit/teardown local logic (write launch, terraform apply/destroy shell-out).
- Create `infra/terraform/aliyun-iam/controller.tf` — controller ECS + restricted RAM role, gated by `enable_controller`.
- Tests under `tests/` mirroring each module.

---

## Task 1: Campaign spec + manifest dataclasses

**Files:**
- Create: `src/clousight_bench/core/campaign_spec.py`
- Test: `tests/test_campaign_spec.py`

**Interfaces:**
- Produces:
  - `@dataclass LaunchSpec{campaign_id:str, tasks:list[str], params:dict, target:dict, watchdog_timeout_s:float=5400.0}` with `to_json()->bytes` / `from_json(bytes)->LaunchSpec`.
  - `@dataclass TaskEntry{task_id:str, status:str="pending", started_ts:float|None=None, ended_ts:float|None=None, error:str|None=None}`.
  - `@dataclass CampaignManifest{campaign_id:str, tasks:list[TaskEntry]}` with `to_json()/from_json()`, `mark(task_id, status, **fields)`, `counts()->dict[str,int]`.

- [ ] **Step 1: Write failing tests** — round-trip `LaunchSpec.from_json(spec.to_json())==spec`; `CampaignManifest.mark("T1.9","running")` flips only that entry and `counts()` reflects it.
- [ ] **Step 2: Run to verify fail** — `pytest tests/test_campaign_spec.py -v` → ImportError.
- [ ] **Step 3: Implement** dataclasses with `json.dumps(asdict).encode()` / `json.loads`. `mark` finds the entry by `task_id`, updates status + given fields (partial-update isolation, like existing campaign manifest).
- [ ] **Step 4: Run tests → PASS.**
- [ ] **Step 5: Commit** `feat(campaign): launch spec + manifest dataclasses`.

---

## Task 2: CampaignChannel (campaign-level OSS objects)

**Files:**
- Create: `src/clousight_bench/domains/agent_runtime/probe/campaign_channel.py`
- Test: `tests/test_campaign_channel.py`

**Interfaces:**
- Consumes: `OssClient` (put/get/list/delete), `LaunchSpec`, `CampaignManifest` (Task 1).
- Produces `CampaignChannel(oss: OssClient, campaign_id: str)` with prefix `clousight-bench/control/{campaign_id}/`, methods:
  - `write_launch(spec)/read_launch()->LaunchSpec|None`
  - `write_manifest(m)/read_manifest()->CampaignManifest|None`
  - `write_heartbeat(current_task:str, phase:str)` (stamps ts via injected `now()` callable) / `read_heartbeat()->dict|None`
  - `write_ledger(raw:bytes)/read_ledger()->bytes|None`
  - `append_log(line:str)` (writes `logs/<zero-padded-seq>.log` objects) / `read_logs()->list[str]`
  - `write_result(task_id, json_bytes, parquet_bytes|None)` / `list_results()->list[str]` / `read_result(task_id)->tuple[bytes,bytes|None]`
  - `write_done(ok:bool)` / `is_done()->str|None` (returns "DONE"/"FAILED"/None)
  - `signal_stop()/stop_requested()->bool`
  - `claim()->bool` (idempotent claim; reuse `OssChannel.claim` semantics — put-if-absent)

- [ ] **Step 1: Write failing tests** using `InMemoryOssClient`: launch round-trip; manifest round-trip; heartbeat with injected `now=lambda:123.0` reads back `{"ts":123.0,...}`; `append_log` twice then `read_logs()` returns both in order; `write_result` with parquet bytes then `read_result` returns both; `write_done(True)` → `is_done()=="DONE"`; `claim()` first True second False.
- [ ] **Step 2: Run → fail (ImportError).**
- [ ] **Step 3: Implement.** Keys: `launch.json`, `status/manifest.json`, `heartbeat.json`, `ledger.json`, `logs/{seq:08d}.log`, `results/{task}.json`, `results/{task}.series.parquet`, `DONE`/`FAILED`, `stop`, `claimed`. `read_*` catch the oss "not found" (get_object raising) → return None. `claim`: `list_prefix` for `claimed`; if absent `put_object` marker, return True; else False. Inject `now: Callable[[],float]` (default `time.time`) for deterministic tests.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(campaign): CampaignChannel OSS objects`.

---

## Task 3: CampaignController orchestration loop

**Files:**
- Create: `src/clousight_bench/core/controller.py`
- Test: `tests/test_controller.py`

**Interfaces:**
- Consumes: `CampaignChannel` (Task 2), `LaunchSpec`/`CampaignManifest` (Task 1), `core/orchestrator.execute`, `ResourceLedger`, `ResultStore`.
- Produces `CampaignController(channel: CampaignChannel, results_dir: Path, run_task: Callable[[str, LaunchSpec], ResultRecord])`. `run()`:
  1. `channel.read_launch()`; init `CampaignManifest` from `spec.tasks`; `write_manifest`.
  2. For each task_id (SERIAL): `manifest.mark(task_id,"running")`+write; `write_heartbeat(task_id,"provision")`; call `run_task(task_id, spec)`; write result JSON+parquet via `channel.write_result`; sync ledger via `channel.write_ledger(ledger_bytes())`; `manifest.mark(task_id, "completed"/"failed")`+write. On exception: mark failed, continue.
  3. On stop signal (`channel.stop_requested()`) between tasks → break.
  4. `channel.write_done(ok = no failures)`.
- `run_task` default wraps `orchestrator.execute` building a `RunSpec` from `spec.target`+`spec.params`; injected as a seam so tests pass a fake returning a canned `ResultRecord`.

- [ ] **Step 1: Write failing test** — `InMemoryOssClient` + `CampaignChannel`; write a `LaunchSpec` with `tasks=["T1.9","T1.13"]`; `run_task` = fake returning a `ResultRecord` (T1.9) and raising for T1.13. Assert after `run()`: manifest has T1.9 completed, T1.13 failed; `is_done()=="FAILED"`; `read_result("T1.9")` present; heartbeat written.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** the loop as above; wrap each `run_task` in try/except recording `error=str(exc)`; write heartbeat before each task.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(controller): serial campaign orchestration loop`.

---

## Task 4: SelfDestructWatchdog

**Files:**
- Create: `src/clousight_bench/core/watchdog.py`
- Test: `tests/test_watchdog.py`

**Interfaces:**
- Consumes: `CampaignChannel`, a `reap: Callable[[], None]`, a `now: Callable[[],float]`.
- Produces `SelfDestructWatchdog(channel, reap, timeout_s, now=time.time)`:
  - `should_stop(start_ts)->str|None` → returns "done"/"timeout"/"stop"/None by checking `channel.is_done()`, `now()-start_ts>timeout_s`, `channel.stop_requested()`.
  - `run_until_terminal(start_ts, poll=lambda:None, sleep=...)` loops calling `poll` then `should_stop`; on non-None calls `reap()` once and returns the reason.

- [ ] **Step 1: Write failing tests** — inject `now` returning increasing stamps; a `reap` spy. (a) `channel.write_done(True)` → `should_stop` returns "done". (b) elapsed>timeout → "timeout". (c) `signal_stop()` → "stop". (d) `run_until_terminal` calls `reap` exactly once and returns the reason.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** as above; guard `reap` with a `_reaped` flag so it fires once.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(watchdog): self-destruct trigger + one-shot reap`.

---

## Task 5: RestrictedReaper (ledger-reverse-lookup delete)

**Files:**
- Create: `src/clousight_bench/domains/agent_runtime/controller_reaper.py`
- Test: `tests/test_controller_reaper.py`

**Interfaces:**
- Consumes: a ledger source (`live_resource_ids()->list[tuple[kind,resource_id]]` derived from `ResourceLedger._events()` created-minus-deleted), delete callables `delete_runtime(id)`, `delete_nat()`, `delete_self_instance(id)`.
- Produces `RestrictedReaper(ledger, deleters)` with `reap()` that deletes in ORDER: all `runtime` ids → NAT/EIP/SNAT → self ECS last; each best-effort (swallow+log), self deleted last.

- [ ] **Step 1: Write failing test** — fake ledger with created runtimes r1,r2 (r2 later marked deleted), a nat, a self id; spy deleters. Assert `reap()` deletes r1 (not r2, already gone), then nat, then self — and self is LAST. Assert order via a recorded call list.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** Compute live set = created − deleted from ledger events; iterate runtimes first, then nat, then self; wrap each delete in try/except appending to an errors list; return errors.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(reaper): restricted ledger-reverse-lookup reap, self last`.

---

## Task 6: cb-controller entrypoint + cloud-init wiring

**Files:**
- Create: `src/clousight_bench/core/controller_main.py`
- Modify: `pyproject.toml` (console_scripts: `cb-controller = clousight_bench.core.controller_main:main`)
- Modify: `src/clousight_bench/domains/agent_runtime/ecs_carrier.py` (add `EcsControllerConfig` or a `code_spec="clousight-bench[probe,store]"` + `start_cmd="cb-controller"` variant of `_build_user_data`)
- Test: `tests/test_controller_main.py`, extend `tests/test_ecs_carrier*.py`

**Interfaces:**
- Consumes: env `CB_CAMPAIGN_ID`, `CB_OSS_BUCKET`, `CB_REGION`; builds `Oss2Client` (via `_EcsMetadataCredentialsProvider` — controller runs under an instance RAM role), `CampaignChannel`, `CampaignController`, `SelfDestructWatchdog`, `RestrictedReaper`.
- Produces `main()`: claim campaign → start controller loop in a thread → watchdog `run_until_terminal` → exit. Reap wired to `RestrictedReaper.reap`.

- [ ] **Step 1: Write failing test** for `controller_main.build(env, oss)` (factory seam that returns wired controller+watchdog WITHOUT running) — assert it constructs a `CampaignController` and `SelfDestructWatchdog` from an `InMemoryOssClient` and a fake env dict. Extend carrier test: `_build_user_data` for the controller variant contains `pip install ... 'clousight-bench[probe,store]'` and `cb-controller`.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `build()` factory + thin `main()` that calls it and runs; add carrier variant param `code_spec`/`start_cmd` threaded into `_build_user_data`; add console script.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(controller): cb-controller entrypoint + [store] cloud-init variant`.

---

## Task 7: Local `submit` command

**Files:**
- Create: `src/clousight_bench/core/prod_submit.py`
- Modify: `src/clousight_bench/cli.py` (`_cmd_submit` + subparser)
- Test: `tests/test_prod_submit.py`

**Interfaces:**
- Consumes: `CampaignChannel`, `LaunchSpec`, a `terraform: Callable[[list[str]], int]` seam (default shells out to `terraform` in the aliyun-iam dir).
- Produces `submit(plan_path, config_path, oss, terraform, *, watchdog_timeout_s)->campaign_id`: loads plan+config (reuse existing plan/config loaders from `_cmd_run_plan`), builds `LaunchSpec`, `channel.write_launch(spec)`, then `terraform(["apply","-auto-approve","-var","enable_controller=true","-var","enable_nat=true","-var",f"campaign_id={cid}"])`.

- [ ] **Step 1: Write failing test** — `InMemoryOssClient` + fake terraform (records argv, returns 0). `submit(plan, config, oss, fake_tf, watchdog_timeout_s=600)` → asserts launch written to OSS with the plan's task-ids, and fake_tf called with `apply ... enable_controller=true enable_nat=true`.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `submit`; reuse the plan-yaml + config-yaml parsing helpers already used by `_cmd_run_plan` (extract to a shared loader if inline). Wire `_cmd_submit` to call it with a real `Oss2Client` + real terraform shell-out.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(cli): prod submit — write launch + terraform apply`.

---

## Task 8: Local `status` / `logs` / `fetch` commands

**Files:**
- Modify: `src/clousight_bench/cli.py` (`_cmd_status/_cmd_logs/_cmd_fetch` + subparsers)
- Extend: `src/clousight_bench/core/prod_submit.py` (pure read helpers)
- Test: `tests/test_prod_readback.py`

**Interfaces:**
- Produces pure functions in prod_submit:
  - `status(channel, now)->dict{counts, current_task, heartbeat_age_s, done}` — reads manifest+heartbeat; `heartbeat_age_s=now()-hb.ts`; flag `stale` if `age > 2*HEARTBEAT_INTERVAL_S`.
  - `logs(channel)->list[str]` — `channel.read_logs()`.
  - `fetch(channel, dest_dir)->list[Path]` — download every `results/*` (JSON + parquet) into `dest_dir`, preserving names; returns written paths.

- [ ] **Step 1: Write failing tests** — seed InMemoryOss with manifest (1 running/1 done), heartbeat ts, two logs, one result JSON+parquet. Assert `status` returns counts + `stale=False` for fresh ts and `True` for old ts; `logs` returns both lines; `fetch(tmp)` writes both result files and returns their paths.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** the three read helpers + wire `_cmd_status`(print JSON/table), `_cmd_logs`(print), `_cmd_fetch`(write dir).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(cli): prod status/logs/fetch readback`.

---

## Task 9: Local `teardown` backstop

**Files:**
- Modify: `src/clousight_bench/cli.py` (`_cmd_teardown` + subparser)
- Extend: `src/clousight_bench/core/prod_submit.py` (`teardown`)
- Test: extend `tests/test_prod_submit.py`

**Interfaces:**
- Produces `teardown(channel, oss, terraform, delete_runtime)->dict{destroyed, residual_deleted}`:
  1. `channel.signal_stop()`.
  2. Pull `channel.read_ledger()` → parse live runtimes → `delete_runtime(id)` each (reverse-lookup residuals independent of controller).
  3. `terraform(["destroy","-auto-approve","-var","enable_controller=false","-var","enable_nat=false"])` (idempotent — already-deleted resources refresh-skip).

- [ ] **Step 1: Write failing test** — seed OSS with a ledger.json listing runtime r9 (created, not deleted); fake terraform + spy delete_runtime. `teardown(...)` → asserts stop signalled, `delete_runtime("r9")` called, terraform `destroy ... enable_nat=false` called.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement**; ledger parse reuses `ResourceLedger` event logic (created−deleted) applied to the OSS-pulled bytes (write to a temp file, load via `ResourceLedger`).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(cli): prod teardown backstop (stop + residual reap + terraform destroy)`.

---

## Task 10: Terraform controller ECS + restricted role

**Files:**
- Create: `infra/terraform/aliyun-iam/controller.tf`
- Modify: `infra/terraform/aliyun-iam/variables.tf` (`enable_controller`, `campaign_id`), `outputs.tf` (controller instance id)
- Test: `tests/test_terraform_controller.py` (runs `terraform validate` if terraform on PATH, else skip)

**Interfaces:**
- Produces: `alicloud_instance.controller` (count = `var.enable_controller?1:0`), attached RAM role `alicloud_ram_role.controller` with a policy granting ONLY: `agentrun:DeleteAgentRuntime*`, `vpc:*NatGateway/*SnatEntry`, `eip:*Address*` (scoped by tag/name where possible), `ecs:DeleteInstance` (self), and OSS read/write on the bench bucket. cloud-init user-data passed via the controller instance `user_data` (built by the carrier variant, or a templated script). `campaign_id` + bucket injected as instance env/tags.

- [ ] **Step 1: Write failing test** — `terraform validate` in the module returns 0 with `enable_controller=true` after adding the file; `terraform plan -var enable_controller=true -var enable_nat=true` shows the controller instance + role to add (skip test if no terraform binary / no creds — `plan` may need creds, so assert on `validate` only).
- [ ] **Step 2: Run → fail** (validate errors: unknown resource) before writing.
- [ ] **Step 3: Implement** controller.tf reusing existing `alicloud_vpc.bench`/`vswitch`/`security_group`; restricted role + policy; wire outputs.
- [ ] **Step 4: Run → `terraform validate` PASS.**
- [ ] **Step 5: Commit** `feat(infra): controller ECS + restricted delete role (terraform)`.

---

## Task 11: dev/prod mode wiring in CLI

**Files:**
- Modify: `src/clousight_bench/cli.py` (mode plumbing), config loaders
- Test: `tests/test_mode_wiring.py`

**Interfaces:**
- `submit` is the prod entrypoint (always prod). Existing `run-plan` keeps the dev path. Add `--mode {dev,prod}` to `run-plan` where `prod` is a hard error telling the user to use `submit` (so the intent naming is enforced and unambiguous), and stamp `mode` into result metadata/environment for provenance.

- [ ] **Step 1: Write failing test** — `run-plan --mode prod` exits non-zero with a message pointing to `submit`; `--mode dev` (default) runs the existing path; result metadata carries `mode="dev"`.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** the flag + guard + provenance stamp.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(cli): dev/prod mode wiring + intent-named guard`.

---

## Task 12: Real-cloud smoke (gated on MAIN-account AK)

**Files:**
- Create: `docs/runbooks/prod-smoke.md` (manual runbook, since it needs live creds)
- Test: manual (documented), plus a `tests/test_prod_e2e_inmemory.py` full-flow test using InMemoryOss + fakes end-to-end (submit → controller.run → status/fetch → teardown) with NO cloud.

**Interfaces:** none new.

- [ ] **Step 1: Write the in-memory end-to-end test** — wire submit(fake tf)+CampaignController(local-sim run_task)+status+fetch+teardown against one `InMemoryOssClient`, asserting the whole loop produces a fetchable result and teardown clears the ledger residuals. This is the CI guarantee.
- [ ] **Step 2: Run → PASS** (after Tasks 1–9 land).
- [ ] **Step 3: Write** `docs/runbooks/prod-smoke.md`: exact commands for a 1–2 light-task (T1.13/T2.1) live campaign with MAIN AK, plus the kill-controller fault-injection check (verify `teardown` clears residuals). Mark it OWNER-RUN (needs MAIN AK + billed NAT).
- [ ] **Step 4: Commit** `test(prod): in-memory end-to-end + live smoke runbook`.

---

## Self-Review

**Spec coverage:** naming(dev/prod)=T11; 二合一 controller=T3/T6; OSS channel layout=T2; parquet+duckdb=reused via ResultStore in T3/T8 (fetch pulls parquet); three-layer safety=T3(per-task)+T4/T5(watchdog+reaper)+T9(teardown); ledger-synced-to-OSS=T3(write_ledger each task)+T9(reads it); MAIN-once terraform create=T7/T10; restricted-role self-destruct=T5/T10; serial-only=T3; testing=every task TDD + T12 e2e. All spec sections mapped.

**Placeholder scan:** no TBD/TODO; each task has concrete interfaces, test assertions, and implementation shape. Delete callables in T5/T9 are injected seams (concrete SDK wiring lives in T6/T10 where the real clients are built).

**Type consistency:** `LaunchSpec`/`CampaignManifest`/`TaskEntry` (T1) used identically in T2/T3/T7. `CampaignChannel` method names fixed in T2 and consumed verbatim in T3/T7/T8/T9. `reap` callable shape consistent T4↔T5↔T6. `terraform` callable shape consistent T7↔T9.
