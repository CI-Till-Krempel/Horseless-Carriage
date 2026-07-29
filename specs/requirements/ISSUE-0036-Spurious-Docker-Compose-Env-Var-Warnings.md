# Issue

- Issue ID: ISSUE-0036
- Title: Spurious "Variable Is Not Set" Warnings For Vars That Already Default Gracefully
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #82): "When running local I experienced
`level=warning msg=\"The \\\"NOTIFICATION_PLUGINS\\\" variable is not set...\"`,
`level=warning msg=\"The \\\"TRANSCRIPT_MAX_ENTRIES\\\" variable is not set...\"`. Have a look why
those variables are not injected."

**Root cause**: `NOTIFICATION_PLUGINS`/`TRANSCRIPT_MAX_ENTRIES` (and, on inspection, several other
vars added the same way by ISSUE-0031/GH issue #76 - `INTERACTION_LEVEL`, `GITHUB_DEVELOP_BRANCH`)
were referenced in both compose files' agent `environment:` as bare `${VAR}`, with no inline
default. Docker Compose prints a "variable is not set... defaulting to a blank string" warning for
*any* bare `${VAR}` reference left unset in `.env` - regardless of whether the application code that
actually reads it already handles that exact case gracefully (e.g.
`get_interaction_level()`/`notifications.get_configured_notifiers()` both already default cleanly).
The variables *were* correctly injected (this repeats ISSUE-0031's fix, not undoing it) - the warning
was simply noise about a case the code was always going to handle fine.

Also found while fixing this: `.env.local.example` didn't define `NOTIFICATION_PLUGINS` at all
(present in `.env.example` but missing from the local-provider template), and neither `.env.example`
nor `.env.local.example` defined `TRANSCRIPT_MAX_ENTRIES` - so *every* fresh setup, cloud or local,
would show this warning on first run, not just a stale `.env`. And ISSUE-0035/GH issue #81 (merged
just before this fix) introduced the exact same pattern for its renamed budget vars
(`TOTAL_USD_BUDGET`, `EVAL_USD_BUDGET_PER_SPRINT`) - any `.env` using only the deprecated old names
(or the current templates, which only define the *new* names) would now show this same warning for
whichever name isn't present.

## Acceptance Criteria
- `NOTIFICATION_PLUGINS`/`TRANSCRIPT_MAX_ENTRIES` no longer produce a Compose "not set" warning when
  absent from `.env` - matching their existing code-side defaults (`"console"` / `200`).
- The same fix applies to every other var in this category found during the review:
  `INTERACTION_LEVEL`, `GITHUB_DEVELOP_BRANCH`, `GITHUB_REPO_BRANCH`, `SPRINT_TOKEN_BUDGET`,
  `PROCESS_OVERHEAD_PERCENTAGE`, `SESSION_ID`.
- The `TOTAL_USD_BUDGET`/`SPRINT_USD_BUDGET` and `EVAL_USD_BUDGET_PER_SPRINT`/`EVAL_SPRINT_USD_BUDGET`
  deprecated-name pairs (ISSUE-0035) also stop warning, **without** breaking their fallback
  resolution - i.e. without ever injecting a real value into the *new* name when only the *old* name
  was actually configured (that would permanently hide the deprecated value, defeating the whole
  point of `get_env_with_deprecated_fallback`).
- Vars with no safe code-side default (credentials, `STATE_REPO_PATH`, `GITHUB_REPO_URL`, the
  eval-only budget vars) are left exactly as before - a warning there is a legitimate signal that
  something genuinely required isn't configured, not noise.
- `.env.local.example` gains the `NOTIFICATION_PLUGINS` line it was missing; both `.env.example` and
  `.env.local.example` gain a `TRANSCRIPT_MAX_ENTRIES` line - so these knobs are discoverable, not
  just silently defaulted.

## Notes
- The fix for the deprecated-name pairs uses an *empty* inline default (`${VAR:-}`), not a real one
  (`${VAR:-10.0}`) - an empty default silences Compose's warning (a default was given) while still
  substituting an empty string if genuinely unset, which is exactly what `get_env_with_deprecated_
  fallback`'s own `if value:` truthiness check needs to correctly fall through to the other name. A
  *real* default here (matching the vars above) would have broken the deprecated-fallback mechanism
  ISSUE-0035 just added, by always populating the new name and permanently hiding whatever the old
  name was set to.
- Verified directly (not just by test) with `docker compose --env-file <file> config`, against three
  scenarios: a fresh `.env.example`/`.env.local.example` copy (zero warnings), and a minimal
  hand-written `.env` missing everything except the truly-required vars (only genuinely-required vars
  still warn - `LITELLM_MASTER_KEY`, `GITHUB_APP_*`, `EVAL_SPRINT_TOKEN_BUDGET`, etc.).

## Test Approach
- `tests/test_docker_compose_env.py::TestNoSpuriousComposeWarningsForVarsWithSafeDefaults` (new) -
  every var in a documented allowlist (`_VARS_EXPECTED_TO_HAVE_INLINE_COMPOSE_DEFAULTS`) uses
  `${VAR:-...}` (not bare `${VAR}`) in both compose files' agent environment, so a future var added
  the same way (code default but no inline compose default) is caught by this test too.
- Manual verification via `docker compose --env-file <file> config` against three `.env` scenarios
  (see Notes) - zero warnings for the vars this fix targets, warnings preserved for genuinely
  required config.
- `pytest tests/`: 290 passed, no regressions.
- Full `agents/scrum_team/tests` suite (via `docker compose --env-file .env.test run --rm
  --entrypoint "" -e PYTHONPATH=/app agent pytest --cov=agents agents/scrum_team/tests`): 248 passed,
  no regressions.

## Resolution
- `docker-compose.yaml` / `docker-compose.local.yaml`: added inline `${VAR:-default}` (matching the
  existing code-side default) for `GITHUB_REPO_BRANCH`, `SPRINT_TOKEN_BUDGET`,
  `PROCESS_OVERHEAD_PERCENTAGE`, `SESSION_ID`, `INTERACTION_LEVEL`, `GITHUB_DEVELOP_BRANCH`,
  `NOTIFICATION_PLUGINS`, `TRANSCRIPT_MAX_ENTRIES`; added an *empty* `${VAR:-}` default for
  `TOTAL_USD_BUDGET`/`SPRINT_USD_BUDGET` and `EVAL_USD_BUDGET_PER_SPRINT`/`EVAL_SPRINT_USD_BUDGET`.
- `.env.example` / `.env.local.example`: added the missing `NOTIFICATION_PLUGINS`/
  `TRANSCRIPT_MAX_ENTRIES` lines.
- `tests/test_docker_compose_env.py`: new `_agent_service_env_entries()` helper and
  `TestNoSpuriousComposeWarningsForVarsWithSafeDefaults` test class.
