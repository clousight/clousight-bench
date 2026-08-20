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
