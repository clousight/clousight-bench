# Mintlify Docs Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the MkDocs Material site (GitHub Pages) with a Mintlify-hosted docs site (MDX + `docs.json`), keeping the task-inventory drift gate and adding a link-check PR gate.

**Architecture:** Convert the nine public nav pages to `.mdx`, author a root `docs.json`, hand-write the API reference, port `scripts/gen_docs.py` from `.md` → `.mdx`, and swap CI (drop the mkdocs build/deploy for a Mintlify `broken-links` check). Mintlify's GitHub App handles hosting/deploy on push to `main`; no deploy workflow lives in-repo.

**Tech Stack:** Mintlify (MDX, `docs.json`, `mint` CLI via npm), Python `scripts/gen_docs.py`, GitHub Actions.

## Global Constraints

- Design doc: `docs/specs/2026-08-20-mintlify-docs-migration-design.md`.
- Repo has NO `origin` remote; all git ops go through `scripts/gitsync.sh` (commit `-m`, push, pr, merge). Commits are `-s` signed with the `clousight-dev` identity (handled by gitsync).
- Stage explicit paths before committing — never blanket-stage untracked files.
- Work on branch `feat/mintlify-docs-migration` (already created off `main`).
- `main` is protected: land via PR; the required gate is the `test` matrix + `wheel-smoke`.
- Nine published pages only: `index`, `architecture`, `plugins`, `probe-carrier`, `querying`, `reporting`, `dataset-tiers`, `reference`, `RELEASING`. Everything else under `docs/` (`specs/`, `plans/`, `runbooks/`, `superpowers/`) stays unpublished — NOT in `docs.json` navigation.
- Brand colors: primary `#2f6df6`, dark `#1e4fd6` (from the report theme `echarts.py`).
- Custom domain `docs.clousight.com` is configured in the Mintlify dashboard, NOT in `docs.json`.
- MDX pages live under `docs/`; `docs.json` at repo root references them by repo-relative path without extension (e.g. `docs/architecture`).
- Internal doc links must be root-relative Mintlify paths without `.md`/`.mdx` (e.g. `/docs/architecture`), not `foo.md`.

---

### Task 1: Port the task-inventory drift gate to `.mdx`

Rename the generated doc to `.mdx`, add frontmatter, and repoint the generator + its test. This is the one task with real automated tests, so it goes first.

**Files:**
- Rename: `docs/architecture.md` → `docs/architecture.mdx`
- Modify: `scripts/gen_docs.py` (the `ARCHITECTURE_DOC` constant + the `.md` in messages)
- Modify: `tests/test_docs_inventory.py` (the `ARCHITECTURE_DOC` constant + assertion string)

**Interfaces:**
- Consumes: `clousight_bench.core.inventory.inventory()` (unchanged).
- Produces: `scripts/gen_docs.py` now reads/writes `docs/architecture.mdx`; `build_doc(current)` and `render_inventory(payload)` signatures unchanged.

- [ ] **Step 1: Rename the file (preserve history)**

```bash
git mv docs/architecture.md docs/architecture.mdx
```

- [ ] **Step 2: Add MDX frontmatter at the very top of `docs/architecture.mdx`**

Insert before the existing `# Architecture` line (then delete that now-duplicate H1, since Mintlify renders `title` as the H1):

```mdx
---
title: Architecture
description: The shared lifecycle, plugin contracts, and evidence model.
---
```

- [ ] **Step 3: Update the failing test to point at `.mdx`**

In `tests/test_docs_inventory.py` change:

```python
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "architecture.mdx"
```

- [ ] **Step 4: Run the test to verify it now FAILS (generator still writes `.md`)**

Run: `pytest tests/test_docs_inventory.py -v`
Expected: FAIL — `gen_docs` still targets `docs/architecture.md` (file no longer exists → FileNotFoundError or stale mismatch).

- [ ] **Step 5: Repoint the generator**

In `scripts/gen_docs.py` change the constant and the two message strings:

```python
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "architecture.mdx"
```

Update the docstring/usage lines that say `docs/architecture.md` to `docs/architecture.mdx` (comments only; no logic change — the `<!-- BEGIN/END generated:task-inventory -->` markers are valid MDX).

- [ ] **Step 6: Run the drift check + tests to verify PASS**

Run:
```bash
python scripts/gen_docs.py --check
pytest tests/test_docs_inventory.py -v
```
Expected: `docs/architecture.mdx: up to date` and all three tests PASS.

- [ ] **Step 7: Commit**

```bash
git add docs/architecture.mdx scripts/gen_docs.py tests/test_docs_inventory.py
scripts/gitsync.sh commit -m "docs: port task-inventory drift gate to architecture.mdx"
```

---

### Task 2: Convert the eight remaining prose pages to `.mdx`

Rename the other public pages, add frontmatter, fix internal links, and convert admonitions. `reference` is handled separately in Task 3 (it's a rewrite, not a conversion).

**Files (rename each `.md` → `.mdx`):**
- `docs/index.md`, `docs/plugins.md`, `docs/probe-carrier.md`, `docs/querying.md`, `docs/reporting.md`, `docs/dataset-tiers.md`, `docs/RELEASING.md`
- (`docs/reference.md` is NOT touched here — Task 3.)

**Interfaces:**
- Produces: seven `.mdx` pages with `title`/`description` frontmatter and Mintlify-valid internal links, ready to be listed in `docs.json` (Task 4).

- [ ] **Step 1: Rename the seven files**

```bash
for f in index plugins probe-carrier querying reporting dataset-tiers RELEASING; do
  git mv "docs/$f.md" "docs/$f.mdx"
done
```

- [ ] **Step 2: Add frontmatter to each page**

Prepend to each file (delete the now-duplicate leading `# Title` H1 in each). Use these exact values:

- `index.mdx`: `title: Clousight Bench` · `description: Reproducible, evidence-graded benchmarking for cloud products.`
- `plugins.mdx`: `title: Extending (plugins)` · `description: Add a platform, dimension, domain, or workload engine.`
- `probe-carrier.mdx`: `title: Probe carrier` · `description: The in-region ECS data-plane probe carrier.`
- `querying.mdx`: `title: Querying results` · `description: DuckDB-backed analytics over the Parquet result store.`
- `reporting.mdx`: `title: Reporting` · `description: Markdown and HTML/ECharts reports.`
- `dataset-tiers.mdx`: `title: Dataset tiers` · `description: Bundled, remote-with-checksum, and private-via-resolver assets.`
- `RELEASING.mdx`: `title: Releasing` · `description: How versions get published.`

Frontmatter block form:
```mdx
---
title: <Title>
description: <Description>
---
```

- [ ] **Step 3: Fix internal cross-links**

In every converted page, rewrite intra-docs links from `name.md` (and `name.md#anchor`) to root-relative Mintlify paths `/docs/name`. Find them:

```bash
grep -rnE '\]\((architecture|plugins|probe-carrier|querying|reporting|dataset-tiers|reference|RELEASING)\.md' docs/*.mdx
```

Example: `[Architecture](architecture.md)` → `[Architecture](/docs/architecture)`. Leave absolute `https://github.com/...` links unchanged.

- [ ] **Step 4: Convert admonitions to Mintlify components**

MkDocs `!!! note` / `!!! warning` blocks (if any) become `<Note>…</Note>` / `<Warning>…</Warning>`. Find them:

```bash
grep -rn '!!!' docs/*.mdx
```

If none are found, this step is a no-op.

- [ ] **Step 5: Give `index.mdx` a card grid ("Learn more" section)**

Replace the bullet list under `## Learn more` in `index.mdx` with a Mintlify card grid:

```mdx
## Learn more

<CardGroup cols={2}>
  <Card title="Architecture" icon="sitemap" href="/docs/architecture">
    The lifecycle, plugin contracts, and evidence model.
  </Card>
  <Card title="Extending (plugins)" icon="puzzle-piece" href="/docs/plugins">
    Add a platform, dimension, domain, or workload engine.
  </Card>
  <Card title="Querying results" icon="database" href="/docs/querying">
    DuckDB-backed analytics over the Parquet store.
  </Card>
  <Card title="API reference" icon="code" href="/docs/reference">
    Core data schema and plugin base classes.
  </Card>
</CardGroup>
```

- [ ] **Step 6: Verify no stale `.md` links remain**

Run: `grep -rnE '\]\([a-z0-9-]+\.md' docs/*.mdx`
Expected: no output (all internal links converted).

- [ ] **Step 7: Commit**

```bash
git add docs/index.mdx docs/plugins.mdx docs/probe-carrier.mdx docs/querying.mdx docs/reporting.mdx docs/dataset-tiers.mdx docs/RELEASING.mdx
scripts/gitsync.sh commit -m "docs: convert prose pages to MDX with frontmatter + card grid"
```

---

### Task 3: Rewrite the API reference as a hand-written page

Replace the mkdocstrings autodoc page with a concise narrative of the core schema and the plugin base classes / entry points. Source of truth for names: `CONTRIBUTING.md` "How to extend" table + `pyproject.toml` `[project.entry-points.*]`.

**Files:**
- Delete: `docs/reference.md`
- Create: `docs/reference.mdx`

**Interfaces:**
- Produces: `docs/reference.mdx` with `title: API reference`, listed in `docs.json` (Task 4).

- [ ] **Step 1: Remove the autodoc page**

```bash
git rm docs/reference.md
```

- [ ] **Step 2: Write `docs/reference.mdx`**

Hand-write these sections (no autodoc directives):

```mdx
---
title: API reference
description: Core data contract and the plugin extension points.
---

The core orchestrates a fixed lifecycle; everything product-specific is a
plugin. This page names the load-bearing types and the entry points that
register them. For line-level code questions, use the in-page **Ask** assistant
or [DeepWiki](https://deepwiki.com/clousight/clousight-bench).

## Result contract

A run emits a `ResultRecord` (schema `0.2`): `status` (`completed` / `failed` /
`invalid` / `unsupported`), `measurements` (each with `value`, `unit`,
`evidence`), `findings` (each with a stable `code`, `severity`, evidence), and
`fingerprints` (`benchmark` / `environment` / `implementation` / `record_digest`).
See [Architecture](/docs/architecture) for the full field table.

## Plugin base classes

| Base class | One per | Registered via |
|---|---|---|
| `DomainPack` | product category | `clousight_bench.domains` |
| `ProviderAdapter` | (domain, cloud) | in the domain pack |
| `Task` | dimension | in the domain pack |
| `WorkloadEngine` | load generator | `manifest.yaml` + executable |

A `Task` implements `config()` (controlled inputs), `execute()` (raw observation
only), `score()` (a pure function of the bundle), and carries `task_revision` /
`scorer_revision`.

## Entry points

Third-party packages extend the harness by declaring these entry points (see
`pyproject.toml`):

- `clousight_bench.domains` — domain packs
- `clousight_bench.runtime_providers` — cloud runtime providers
- `clousight_bench.resource_reapers` — orphan-resource reapers
- `clousight_bench.enrichers` — post-run enrichers (e.g. pricing)
- `clousight_bench.span_exporters` — trace span exporters

See [Extending (plugins)](/docs/plugins) for a worked example.
```

- [ ] **Step 3: Verify the page has no autodoc directives**

Run: `grep -n ':::' docs/reference.mdx`
Expected: no output (no mkdocstrings `::: module` directives).

- [ ] **Step 4: Commit**

```bash
git add docs/reference.mdx
scripts/gitsync.sh commit -m "docs: hand-write the API reference page (drop mkdocstrings autodoc)"
```

---

### Task 4: Author `docs.json`

Create the Mintlify config: theme, brand colors, navigation (the nine pages), navbar/footer links, and versioning.

**Files:**
- Create: `docs.json` (repo root)

**Interfaces:**
- Consumes: the nine `.mdx` pages from Tasks 1–3 (by repo-relative path without extension).
- Produces: `docs.json` — the only file Mintlify's onboarding "Use existing" checks for on `main`.

- [ ] **Step 1: Write `docs.json`**

```json
{
  "$schema": "https://mintlify.com/docs.json",
  "theme": "mint",
  "name": "Clousight Bench",
  "colors": {
    "primary": "#2f6df6",
    "light": "#5a8bf8",
    "dark": "#1e4fd6"
  },
  "favicon": "/docs/favicon.png",
  "navigation": {
    "versions": [
      {
        "version": "latest",
        "groups": [
          {
            "group": "Get started",
            "pages": ["docs/index", "docs/architecture"]
          },
          {
            "group": "Guides",
            "pages": [
              "docs/plugins",
              "docs/probe-carrier",
              "docs/querying",
              "docs/reporting",
              "docs/dataset-tiers"
            ]
          },
          {
            "group": "Reference",
            "pages": ["docs/reference", "docs/RELEASING"]
          }
        ]
      }
    ]
  },
  "navbar": {
    "links": [
      { "label": "DeepWiki", "href": "https://deepwiki.com/clousight/clousight-bench" }
    ],
    "primary": {
      "type": "button",
      "label": "GitHub",
      "href": "https://github.com/clousight/clousight-bench"
    }
  },
  "footer": {
    "socials": {
      "github": "https://github.com/clousight/clousight-bench"
    }
  }
}
```

Note: `favicon` path assumes `docs/favicon.png` exists; if not, drop the
`favicon` key (Mintlify uses a default). Check with `ls docs/favicon.png` and
remove the line if absent.

- [ ] **Step 2: Validate `docs.json` is well-formed JSON**

Run: `python -c "import json; json.load(open('docs.json')); print('docs.json OK')"`
Expected: `docs.json OK`.

- [ ] **Step 3: Commit**

```bash
git add docs.json
scripts/gitsync.sh commit -m "docs: add Mintlify docs.json (nav, brand, versioning)"
```

---

### Task 5: Swap CI and packaging off MkDocs

Replace the `docs` CI job (mkdocs strict build) with a Mintlify link check, delete the Pages deploy workflow, remove the `[docs]` extra, and delete `mkdocs.yml`.

**Files:**
- Modify: `.github/workflows/ci.yml` (the `docs` job, lines ~202-216)
- Delete: `.github/workflows/docs.yml`
- Delete: `mkdocs.yml`
- Modify: `pyproject.toml` (remove the `docs` extra, lines 81-84)

**Interfaces:**
- Consumes: `docs.json` + `.mdx` pages (Tasks 1–4).
- Produces: a `docs` CI job that runs `mint broken-links`; no in-repo docs deploy (Mintlify GitHub App owns deploy).

- [ ] **Step 1: Replace the `docs` job in `.github/workflows/ci.yml`**

Replace the existing `docs:` job body with a Node-based Mintlify link check:

```yaml
  docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - name: Install Mintlify CLI
        run: npm i -g mint
      - name: Check for broken links
        run: mint broken-links
      - name: Docs inventory up to date
        run: |
          python -m pip install --upgrade pip
          pip install -e .
          python scripts/gen_docs.py --check
```

(The inventory check also runs in the `test` job; keeping it here makes the docs
job self-contained. If `mint broken-links` requires a build first, prepend
`mint build` — verify against the Mintlify CLI docs during implementation.)

- [ ] **Step 2: Delete the GitHub Pages deploy workflow**

```bash
git rm .github/workflows/docs.yml
```

- [ ] **Step 3: Delete `mkdocs.yml`**

```bash
git rm mkdocs.yml
```

- [ ] **Step 4: Remove the `[docs]` extra from `pyproject.toml`**

Delete these lines from `[project.optional-dependencies]`:

```toml
docs = [
    "mkdocs-material>=9.5",
    "mkdocstrings[python]>=0.25",
]
```

- [ ] **Step 5: Verify no MkDocs references remain**

Run:
```bash
grep -rniE 'mkdocs|mkdocstrings' . --include='*.yml' --include='*.yaml' --include='*.toml' --include='*.md' --include='*.mdx' | grep -v 'docs/specs\|docs/plans'
```
Expected: no output (design/plan docs under `docs/specs`,`docs/plans` may mention it historically; those are excluded).

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml pyproject.toml
scripts/gitsync.sh commit -m "ci: swap mkdocs build/deploy for a Mintlify broken-links gate"
```

---

### Task 6: README badge + setup runbook

Point the docs badge at the new domain and document the owner/dashboard steps that cannot be done in-repo.

**Files:**
- Modify: `README.md` (the Docs badge line — added in PR #58; if PR #58 is not yet merged, add it here)
- Create: `docs/runbooks/mintlify-setup.md`

**Interfaces:**
- Produces: the operator runbook referenced by the spec's Sequencing section.

- [ ] **Step 1: Point the Docs badge at the new domain**

In `README.md`, set the Docs badge target to `https://docs.clousight.com`:

```markdown
[![Docs](https://img.shields.io/badge/docs-docs.clousight.com-blue.svg)](https://docs.clousight.com)
```

(If PR #58's Docs badge line isn't on this branch yet, add the badge line under the License badge.)

- [ ] **Step 2: Write `docs/runbooks/mintlify-setup.md`**

```markdown
# Mintlify docs site — one-time setup

These steps live outside the repo (account + dashboard + DNS). Do them AFTER the
migration PR (which adds `docs.json`) merges to `main`.

1. **Account.** Sign in at mintlify.com with the org Google account
   ("Continue with Google"). Sign-in offers only Google or email+password — no
   GitHub login. Avoid an Apple `@privaterelay.appleid.com` per-app relay (it may
   not receive verification mail).
2. **Onboarding update method:** choose **Local development** (docs-as-code).
3. **OSS Program:** apply for Pro (Apache-2.0, non-commercial qualifies) to
   unlock the AI assistant, per-PR previews, and analytics. Until granted, the
   free Starter tier still auto-publishes on push — no rework needed later.
4. **Install the GitHub App:** into the **`clousight` org**, scoped to **only
   `clousight-bench`**. Requires org Owner (else Request → owner approval).
   "Include private" is not needed (repo is public).
5. **Connect the repo:** onboarding → **Use existing** → `clousight/clousight-bench`.
   This now works because `docs.json` is on `main`.
6. **Custom domain:** set `docs.clousight.com` in the dashboard and add the DNS
   CNAME it shows.
7. **Enable versioning and the AI assistant** in the dashboard (Pro).

Rollback: the old `clousight.github.io/clousight-bench` Pages site still serves
its last build. To fully revert, restore `mkdocs.yml`, the `[docs]` extra, and
`.github/workflows/docs.yml` from git history.
```

- [ ] **Step 3: Commit**

```bash
git add README.md docs/runbooks/mintlify-setup.md
scripts/gitsync.sh commit -m "docs: point Docs badge at docs.clousight.com + Mintlify setup runbook"
```

---

### Task 7: Full-suite verification + open PR

**Files:** none (verification + PR).

- [ ] **Step 1: Run the drift gate and the docs-inventory tests**

```bash
python scripts/gen_docs.py --check
pytest tests/test_docs_inventory.py -q
```
Expected: up to date; tests PASS.

- [ ] **Step 2: Run the fast test suite (nothing product-side changed, but confirm)**

Run: `pytest -q`
Expected: PASS.

- [ ] **Step 3: (Optional, if Node available) local link check**

```bash
npm i -g mint && mint broken-links
```
Expected: no broken links. If `mint` isn't installed locally, rely on the CI `docs` job.

- [ ] **Step 4: Push and open the PR**

```bash
scripts/gitsync.sh push
scripts/gitsync.sh pr "docs: migrate documentation site from MkDocs to Mintlify" "Implements docs/specs/2026-08-20-mintlify-docs-migration-design.md.

- 9 pages converted to MDX + docs.json (nav, brand, versioning)
- API reference hand-written (drops mkdocstrings autodoc)
- gen_docs drift gate ported to architecture.mdx (tests updated)
- CI: mkdocs strict build/deploy -> Mintlify broken-links gate; docs.yml + mkdocs.yml removed; [docs] extra dropped
- README Docs badge -> docs.clousight.com; docs/runbooks/mintlify-setup.md added

Site goes live once the owner completes docs/runbooks/mintlify-setup.md (account, OSS Pro, GitHub App, DNS).

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

- [ ] **Step 5: Report the PR URL and remaining owner steps to the user.**

---

## Self-Review

**Spec coverage:**
- Content & structure (9 pages → mdx, internal specs unpublished) → Tasks 1–4.
- `docs.json` (nav, brand, versioning) → Task 4.
- Build/deploy/CI (drop docs.yml, mint broken-links, gen_docs port, remove [docs], delete mkdocs.yml) → Tasks 1, 5.
- Hand-written reference → Task 3.
- Custom domain + runbook + README badge → Task 6.
- Sequencing (docs.json on main first) → Task 7 PR; runbook step 5.
- Free-vs-Pro / OSS launch strategy → captured in runbook (Task 6 step 2).

**Placeholder scan:** `favicon` path is conditionally handled (Task 4 step 1 note). The `mint broken-links` vs `mint build` detail is flagged for verification in Task 5 step 1 — acceptable because the CLI subcommand is externally documented, not repo-internal.

**Type consistency:** `ARCHITECTURE_DOC` renamed consistently in `gen_docs.py` and `tests/test_docs_inventory.py` (Task 1). `build_doc` / `render_inventory` signatures unchanged. Page paths in `docs.json` (`docs/<name>`) match the renamed `.mdx` files and the `/docs/<name>` internal links.
