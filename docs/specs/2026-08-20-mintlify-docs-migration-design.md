# Docs platform migration: MkDocs → Mintlify

Date: 2026-08-20
Status: design (approved in brainstorming; pending spec review)

## Goal

Replace the self-hosted MkDocs Material site (deployed to GitHub Pages) with a
Mintlify-hosted documentation site, adopting the conventions of
`agentscope-ai/agentscope` (which also runs on Mintlify). The move buys four
things the maintainer explicitly wants:

1. **Better visual / brand** — card-based landing page, modern components.
2. **AI "Ask" assistant** — a conversational docs assistant (Mintlify Pro).
3. **Versioned docs** — multiple releases side by side (`/versions/...`).
4. **Custom domain / managed hosting** — `docs.clousight.com`, off `github.io`.

## Platform & plan

- **Mintlify Pro via the OSS Program.** clousight-bench is Apache-2.0 and
  non-commercial, which qualifies. Pro (via OSS) unlocks the AI assistant,
  preview deployments and analytics.
- Docs authored in **MDX** with a root **`docs.json`**; hosted on Mintlify's
  SaaS; deployed by the **Mintlify GitHub App** on push to `main`.

### Reference: what agentscope actually runs

Verified against the live `docs.agentscope.io` DOM (2026-08-20): `generator =
Mintlify`, an AI chat assistant is present (`chat-assistant-*` elements — a
**Pro** feature), a version selector is live, and the **"Powered by Mintlify"**
footer is still shown (so **not Enterprise** — only Enterprise removes it).
Conclusion: agentscope is on **Pro**, almost certainly free via the OSS Program.
Matching it (AI Ask + versioning + custom domain) means Pro; the only thing we
cannot remove without Enterprise is the footer badge (acceptable).

### Free vs Pro (Starter), confirmed from mintlify.com/pricing

- **Auto-publish works on the free Starter plan.** Git sync (GitHub App
  auto-deploy on push to `main`) is on both tiers — publishing is never manual.
- Starter includes: custom domain, 5 editor seats, ⌘K search, git sync.
- Pro-only (missing on Starter): **AI assistant**, **per-PR preview
  deployments**, **analytics/insights**, webhooks/developer API, user-feedback
  widgets. Versioning tier is unconfirmed from the pricing table (verify during
  setup); the OSS Pro grant makes it moot.
- The **"Powered by Mintlify"** badge stays on both Starter and Pro; removing it
  needs Enterprise.

**Launch strategy:** the migration PR is tier-agnostic (`docs.json` + `.mdx` +
CI are identical on any plan). Ship on Starter immediately if OSS approval lags —
auto-publish still works — and enable AI Ask + previews once Pro is granted, with
**zero repo rework**.

## Non-goals

- Porting internal design docs (`docs/specs`, `docs/plans`, `docs/superpowers`,
  `docs/runbooks`) into the published site. They stay in-repo, unpublished.
- Auto-generating a Python API reference. Mintlify has no Python autodoc (only
  OpenAPI). The API reference becomes a hand-written narrative page; fine-grained
  code Q&A is served by DeepWiki (already wired) + the Mintlify "Ask" assistant.
- Any change to product/runtime code. This is docs + CI only.

## Design

### 1. Content & structure

Convert the nine public nav pages from `.md` → `.mdx`, each with YAML
frontmatter (`title`, `description`):

`index`, `architecture`, `plugins`, `probe-carrier`, `querying`, `reporting`,
`dataset-tiers`, `reference`, `RELEASING`.

- Landing page (`index.mdx`) gets a `<Card>`/`<CardGroup>` grid (agentscope
  style) linking to the main sections.
- Existing MkDocs admonitions become Mintlify `<Note>` / `<Warning>`.
- `reference.mdx` is **rewritten by hand** as a concise "core API & extension
  points" guide (Task / ProviderAdapter / DomainPack / WorkloadEngine and the
  entry points), replacing the mkdocstrings autodoc dump.
- Content is plain markdown today, and MDX is a markdown superset, so prose
  ports with minimal change beyond frontmatter + the components above.

Internal design docs are **not** listed in `docs.json` → Mintlify only builds
pages present in navigation, so they remain in-repo but unpublished.

### 2. `docs.json`

Root `docs.json` defines:

- `name`, `theme`, brand `colors` (reuse the report brand palette).
- `navigation` groups mirroring today's nav order.
- `navbar`/`footer` links: GitHub repo, DeepWiki, clousight.com.
- `versioning` config (enabled).
- Custom domain handled in the Mintlify dashboard, not `docs.json`.

### 3. Build, deploy & CI

- **Deploy**: the Mintlify GitHub App builds + hosts on push to `main`. Our
  `.github/workflows/docs.yml` (GitHub Pages deploy) is **removed**.
- **PR gate**: replace `mkdocs build --strict` with the Mintlify CLI link check
  (`mint broken-links`) run in CI on PRs, so dead links still fail a PR.
- **Drift gate preserved**: `scripts/gen_docs.py --check` stays a CI gate; its
  target changes from `docs/architecture.md` → `docs/architecture.mdx`. The
  `<!-- BEGIN/END generated:task-inventory -->` markers are valid in MDX, so the
  splice logic is unchanged (only the file path + extension).
- **Dependency cleanup**: remove the `[docs]` extra (`mkdocs-material`,
  `mkdocstrings[python]`) from `pyproject.toml`; delete `mkdocs.yml`.
- README "Docs" badge points at the new domain.

### 4. Custom domain

`docs.clousight.com` → CNAME to Mintlify (owner action; documented in runbook).
The old `clousight.github.io/clousight-bench` keeps its last-built artifact
served (Pages source can be turned off later); no live-docs outage window.

### 5. Runbook (human / dashboard steps)

A new `docs/runbooks/mintlify-setup.md` captures the steps that cannot be done
in-repo:

1. Register on mintlify.com. **Login: "Continue with Google" using the org
   Google account** (Mintlify's sign-in offers only Google or email+password —
   no GitHub login; avoid the Apple `@privaterelay.appleid.com` per-app relay,
   which may not receive verification mail). *(Done by owner.)*
2. Onboarding: choose **Local development** (docs-as-code via Git/CLI).
3. Apply to the **OSS Program** for Pro.
4. Install the **Mintlify GitHub App** into the **`clousight` org**, scoped to
   **only `clousight-bench`** (requires org Owner, else Request → owner
   approval). "Include private" not needed (repo is public).
5. Connect the repo with **"Use existing"** — this requires `docs.json` on the
   default branch, which is why the migration PR lands **first** (see
   Sequencing).
6. Set the custom domain `docs.clousight.com` + add the DNS CNAME.
7. Enable **versioning** and the **AI assistant** in the dashboard.

### 6. Sequencing

1. Land the migration PR to `main` (adds `docs.json` + `.mdx`, ports gen_docs,
   swaps CI, removes mkdocs).
2. Owner then connects Mintlify via "Use existing" — `docs.json` now present, so
   the "no docs.json found" blocker is gone — installs the GitHub App, sets DNS,
   enables versioning + AI.

This ordering is deliberate: Mintlify's "Use existing" onboarding refuses a repo
whose default branch has no `docs.json`.

## Risks & mitigations

- **Loss of Python autodoc.** Mitigated: hand-written reference page + DeepWiki +
  Mintlify Ask cover code-level questions. The autodoc dump was low-value prose.
- **Stale `github.io` window.** After the PR merges and before DNS resolves,
  `docs.clousight.com` isn't live yet; the old Pages site still serves its last
  build. Acceptable for a developer-preview project.
- **Org-owner permission for the GitHub App.** If the operator isn't a
  `clousight` org Owner, the App install becomes a Request needing owner
  approval. Called out in the runbook.
- **OSS Program approval latency.** Until Pro is granted, the site works on
  Starter (visuals + custom domain) without the AI assistant; the assistant
  turns on once Pro lands. No hard dependency in the migration PR.

## Verification

- `python scripts/gen_docs.py --check` passes against `architecture.mdx`.
- `mint broken-links` passes locally over the converted docs.
- `docs.json` validates (`mint` CLI).
- No remaining references to `mkdocs` in CI, `pyproject.toml`, or repo tree.
- README Docs badge resolves to the new domain.
