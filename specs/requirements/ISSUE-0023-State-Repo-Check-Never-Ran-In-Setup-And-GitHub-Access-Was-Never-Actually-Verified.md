# Issue

- Issue ID: ISSUE-0023
- Title: State Repo Check Never Ran in Setup, and GitHub Access Was Never Actually Verified
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #60), two related gaps:

1. **`check_state_repo.py` was never wired into the setup process.** It's a fully standalone script,
   documented in README/MANUAL/STATE-REPOSITORY.md, but neither `setup_all.py`'s guided walkthrough
   nor `doctor.py`'s gate ever ran it (or its checks) - a user had to remember it existed and run it
   by hand, and easily wouldn't.
2. **GitHub read/write access was never actually verified.** `doctor.py`'s GitHub section only ever
   checked that a credential was *present* in `.env` (`GITHUB_TOKEN`, or the `GITHUB_APP_*` trio) -
   never that it could actually read or write the target repo's issues and pull requests, which the
   issue calls out as "critical to the whole setup." This matters more than it might look: a
   fine-grained personal access token (or a GitHub App installation) can restrict Issues and Pull
   requests access independently of each other and of the repo's overall push permission - so a
   token that looks fully configured can still fail at the exact operations the agents depend on.
   (Concretely reproduced during this same session: a fine-grained PAT with full PR read/write still
   got `Resource not accessible by personal access token` on `closeIssue`/`addComment` - Issues access
   had never been granted, despite PRs working fine.)

The issue also suggested simplifying GitHub App installation (e.g. via the GitHub API or a browser
authorization flow) - out of scope for this change (see Notes), but the live access check above at
least tells a user immediately whether their existing setup - App or token - actually works, rather
than only failing much later when a real issue/PR write is attempted.

## Acceptance Criteria
- `setup_all.py`'s guided flow runs `check_state_repo.py` right after `setup_llm.py` (which
  creates/clones the state repository), with the same fix-and-retry loop as its other guided steps.
- `doctor.py` runs the cheap, filesystem-only part of `check_state_repo.py`'s checks itself (`specs/`
  directory presence, stray `TEMPLATE-*.md` files) on every invocation - so a broken state repo shows
  up in the punch list `run.py`'s pre-flight gate and `setup_all.py`'s doctor gate already rely on,
  not only when a user remembers to run `check_state_repo.py` directly. The heavier `state.json`
  validation (which can shell out to Docker) stays in `check_state_repo.py` only - too expensive to
  run on every `run.py` start.
- Given `GITHUB_REPO_URL` and a resolvable credential (`GITHUB_TOKEN`, or a GitHub App token minted
  from the `GITHUB_APP_*` trio), `doctor.py` performs a live, read-only check: reads the repo, lists
  its issues, lists its pull requests, and reports the repo-level `permissions.push` flag - warning
  loudly if any of these fail, rather than only reporting "a credential is configured."
- None of the above run when there's nothing to check (no `GITHUB_REPO_URL`, no auth method
  configured, an unparseable repo URL) or when it's not yet meaningful (`run.py`'s pre-flight gate,
  `skip_llm_probe=True`, same reasoning as the existing LLM proxy-reachability gating).

## Notes
- Where the gaps lived: `setup_all.py`'s step list never included `check_state_repo.py`;
  `doctor.py`'s GitHub section (previously just an if/elif/else over which env vars were set) never
  made a live API call at all.
- `auth_github.py`'s JWT-signing + token-exchange logic was factored out into a new
  `mint_installation_token()` function so `lib_github.py` could reuse it read-only (mint a token to
  test with) without triggering `auth_github.py main()`'s side effect of logging the host/container's
  own `gh` CLI in as the app - that would be a surprising thing for a diagnostic check to do.
- **Not attempted**: an actual write-access test (e.g. creating and deleting a throwaway issue). There
  is no safe, side-effect-free way to verify write access to a specific GitHub resource type without
  actually performing a write, and a "doctor" check performing invasive/destructive actions as a side
  effect of a routine diagnostic run would be surprising and unwelcome (as would asking the token to
  self-report per-resource-type scopes, which fine-grained PATs don't expose via a simple endpoint) -
  the repo-level `permissions.push` flag is reported as the closest available proxy, with an explicit
  caveat that it isn't a full guarantee.
- **Not attempted**: automating GitHub App creation/installation itself (the issue's "would be great
  if" suggestion). GitHub's App Manifest flow could plausibly automate the "create the app + get back
  App ID/private key" part via a local callback server + browser authorization, but installing the
  app on the target repo and capturing the resulting Installation ID automatically needs more
  plumbing (a `setup_url` + `setup_on_update` redirect capture) than fits this change alongside the
  two fixes above; worth a follow-up issue of its own rather than folding into this one.

## Test Approach
- `tests/test_lib_github.py` - unit tests for `parse_owner_repo` (SSH/HTTPS URL forms, non-GitHub
  URLs), `resolve_token` (token takes priority over App trio, incomplete trio, successful App-token
  mint via a mocked `auth_github.mint_installation_token`, mint failure), and `check_repo_access`
  (all reads succeed with/without push permission, each of the three calls failing independently
  short-circuits the rest).
- `tests/test_doctor.py::TestStateRepoStructureChecks` - missing `specs/` dir warns, stray templates
  warn, both point at `check_state_repo.py` for the fuller check, and a repo with no `STATE_REPO_PATH`
  at all doesn't crash trying to inspect a specs dir that was never named.
- `tests/test_doctor.py::TestGithubAccessCheck` - the check is skipped (silently, or with a specific
  warning for a mint failure) when there's no repo URL, no auth configured, an unparseable repo URL,
  or `skip_llm_probe=True`; confirmed access prints and doesn't warn; a failed read warns with the
  underlying detail.
- `tests/test_setup_all.py` - `check_state_repo.py` runs after `setup_llm.py` and before
  `setup_project.py` in `main()`'s step order, and a failure there (with the retry prompt declined)
  stops before `setup_project.py` runs, matching the existing guided-step failure behavior.
- Full `pytest tests/` run (243 passed) confirms no regressions elsewhere.

## Resolution
- `auth_github.py`: extracted `mint_installation_token(app_id, private_key, installation_id)` from
  `main()` - the pure JWT-sign + token-exchange steps, with no `gh auth login` side effect.
- Added `lib_github.py`: `parse_owner_repo()`, `resolve_token()` (GITHUB_TOKEN, or a minted GitHub App
  token, degrading gracefully if PyJWT/requests aren't available or minting fails), and
  `check_repo_access()` (the three-call read/permissions check described above).
- `check_state_repo.py`: extracted `stray_template_files(specs_dir)` so `doctor.py` can reuse the
  same check without duplicating it.
- `doctor.py`: added the live GitHub access check (gated as described above) and the cheap state-repo
  structure checks (specs/ dir presence, stray templates) alongside the existing STATE_REPO_PATH
  directory-existence check.
- `setup_all.py`: added `check_state_repo.py` as a guided step between `setup_llm.py` and
  `setup_project.py`.
- Updated `docs/STATE-REPOSITORY.md` and `docs/GITHUB-INTEGRATION.md` to describe both checks.
