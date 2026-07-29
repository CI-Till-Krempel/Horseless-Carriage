# Issue

- Issue ID: ISSUE-0031
- Title: INTERACTION_LEVEL (and Other Env Vars) Missing From the Agent Container's Environment
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #76): `INTERACTION_LEVEL` was set to `"Stakeholder"` in `.env`, but the running
agent read it back as `"Product"` (the default). "Please make sure all items are read correctly and
passed into the agent container."

**Root cause**: `docker-compose.yaml`/`docker-compose.local.yaml`'s `agent` service `environment:`
list is an explicit whitelist - Docker Compose only forwards variables listed there into the
container, however plausible it looks that the rest of `.env` would come along automatically (it
does not; Compose only reads `.env` on the *host* side to resolve `${VAR}` references used inside
that same file). `INTERACTION_LEVEL` was never listed in either compose file's `agent` service, so
`os.getenv("INTERACTION_LEVEL")` inside the container was always empty regardless of `.env`, and
`get_interaction_level()` (`agents/scrum_team/helpers.py`) correctly falls back to `"Product"` for
exactly that reason - the fallback logic itself was never the bug.

Auditing every `os.getenv`/`os.environ.get` call in `agents/scrum_team/` and `auth_github.py` against
both compose files' `agent` environment lists found three more of the same gap:
`GITHUB_DEVELOP_BRANCH` (GitFlow's develop-branch name), `NOTIFICATION_PLUGINS` (GH issue #53's
notifier plugin selection), and `TRANSCRIPT_MAX_ENTRIES` (transcript trimming threshold) - all
legitimately `.env`-configurable, all silently never reaching the container.

## Acceptance Criteria
- `INTERACTION_LEVEL`, `GITHUB_DEVELOP_BRANCH`, `NOTIFICATION_PLUGINS`, and `TRANSCRIPT_MAX_ENTRIES`
  are added to the `agent` service's `environment:` list in both `docker-compose.yaml` and
  `docker-compose.local.yaml`.
- A regression test scans the actual agent code for every env var it reads and asserts each one
  (except a documented allowlist of container-internal/CI-only vars never sourced from a user's
  `.env`) is present in both compose files - so a *future* new env var read in code and forgotten in
  the compose files fails this test too, not just this specific incident.

## Notes
- Deliberately excluded from the "must be passed through" audit: `INTERNAL_STATE_REPO_PATH`
  (container-internal-only, already hardcoded to `/app/state_repo` in both files, never sourced from
  the host's own env), and `GITHUB_ACTIONS`/`GITHUB_REPOSITORY`/`GITHUB_RUN_ID`/`GITHUB_SERVER_URL`/
  `EVAL_RUN_ID` (auto-set by the GitHub Actions runner or the eval harness's own internal
  run-isolation bookkeeping - never a user's own `.env` value, and not applicable via
  `docker-compose` at all).
- This is a plain host-side config file bug (YAML), not application code - `os.getenv`'s own
  behavior was correct throughout; nothing in `get_interaction_level()`/`helpers.py` needed to
  change.

## Test Approach
- `tests/test_docker_compose_env.py` (new): scans `agents/scrum_team/**/*.py` + `auth_github.py` for
  every `os.getenv`/`os.environ.get("SOME_VAR")` call, and asserts each one (minus the documented
  allowlist above) is present in both compose files' `agent` service `environment:` list. A
  dedicated test also asserts `INTERACTION_LEVEL` specifically is present, matching the exact
  reported symptom.
- Verified the new test actually catches the bug: reverted both compose files locally and confirmed
  all three new tests fail, listing exactly the four missing variables, before restoring the fix.
- `docker compose -f docker-compose.yaml config --quiet` / `-f docker-compose.local.yaml` confirm
  both files still parse as valid Compose configuration.
- `pytest tests/` (full host-side suite): 288 passed, no regressions.

## Resolution
- `docker-compose.yaml`, `docker-compose.local.yaml`: added `INTERACTION_LEVEL`,
  `GITHUB_DEVELOP_BRANCH`, `NOTIFICATION_PLUGINS`, `TRANSCRIPT_MAX_ENTRIES` to the `agent` service's
  `environment:` list.
- `tests/test_docker_compose_env.py` (new): the completeness regression test described above.
