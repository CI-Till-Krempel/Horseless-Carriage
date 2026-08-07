# Issue

- Issue ID: ISSUE-0047
- Title: Pytest Can't Import Root-Level App Modules From tests/, Burning A Whole Sprint On A QA/DevTeam Retry Loop - Plus A Roadmap Version-Tagging Desync
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-08-07

## Overview
Reported (maintainer, reviewing `0.1.0-run27` - the first run after ISSUE-0046's fixes): the results were
"not good" - Code Quality scored 1/5 ("no source code files ... whatsoever"), Team Efficiency 1/5, and
5,254,864 tokens were spent producing zero merged implementation.

Investigated against the real run: `gh run view 31171564210` (`0.1.0-run27`), its
`report.md`/`manifest.json`/`transcript.md` artifacts, and the actual PRs/files in the eval repo. Two
distinct findings.

**Finding 1 (the dominant cause) - pytest cannot import a root-level app module from `tests/`, and
QA/DevTeam bounced on it 9 times, burning the sprint's entire event AND token budget:** the manifest
shows `stop_reason: max_events_reached` (the hard 300-ADK-event-per-sprint safety cap, `run_eval.py`'s
`--max-events-per-sprint`) with `critical_halt: True` - this sprint didn't run out of nudges or grace,
it ran out of *events*, before any close-out sequence could even be attempted. QA called `check_build`
12 times and `advance_story_stage` 12 times; DevTeam called `write_file` 12 times, rewriting
`tests/test_app.py` 9 times. Traced in the transcript: `check_build` (a real `pip install`) passes
every time, but `advance_story_stage(..., "Tested")` keeps rejecting with an identical
`ModuleNotFoundError: No module named 'app'` from pytest - `app.py` lives at the repo root, `test_app.py`
does `from app import app, lists` from `tests/test_app.py`, and pytest's default "prepend" import mode
(no `tests/__init__.py`, no `conftest.py`/`pytest.ini` at repo root) inserts each test file's own
containing directory (`tests/`) onto `sys.path`, not the repo root - so the import can never resolve
regardless of `cwd`. DevTeam's fix attempts (adding `sys.path.insert(0, os.path.dirname(__file__))`
*inside* `test_app.py` itself) only ever re-inserted `tests/`'s own directory - already implicitly
there - never the actual gap. `check_build`'s own docstring already documents a *sibling* case of this
exact symptom (a real eval run bounced 9 times on `ModuleNotFoundError` from an unresolvable pinned
third-party dependency version, fixed by making `check_build` do a real `pip install` instead of a dry
run) - this run hit the same failure signature from a different, harness-level root cause: nothing in
the pipeline ever put the project's own root on `sys.path` for the test run itself.

**Finding 2 - a roadmap version-tagging desync, visible in the report's own "Requirements Quality"
complaint:** `specs/ROADMAP.md` showed the correct stories, correctly checked off, under
"Backlog (unplanned)" - but a second, stuck, all-unchecked, placeholder-titled duplicate
(`[US-0001] US-0001`) under a `### v1.0.0` section. Root cause: `update_roadmap(version, stories=[...])`
- the tool `prompts.py` explicitly tells Product Owner to use for top-down release planning, before
individual stories are even fleshed out - never tagged the matched `product_backlog` items with
`version = "v1.0.0"`. `_sync_roadmap_for_story` (the automatic re-sync `advance_story_stage` triggers on
every stage transition) keys off exactly that field, defaulting to "Backlog (unplanned)" when unset - so
every later, real sync kept updating the *other* section, leaving the `v1.0.0` section a permanently
stale one-time snapshot from before the stories had real titles.

## Acceptance Criteria
- `_execute_test_suite_coverage` (used by both `advance_story_stage`'s Tested gate and
  `calculate_kpis`) runs pytest with `PYTHONPATH` explicitly set to the target repo's root (prepended
  ahead of any existing value) - not left to whichever generated project's own `pytest.ini`/
  `conftest.py` may or may not exist - so a completely normal Flask-style layout (root-level `app.py`,
  tests under `tests/`) doesn't hit an unresolvable `ModuleNotFoundError` regardless of how many times a
  model rewrites the test file itself.
- `update_roadmap` tags every story it places under a version (via its own `stories` argument, not just
  `plan_backlog_item`) with that `version` on the matching `product_backlog` item, so later automatic
  re-syncs keep targeting the same, correct section instead of silently diverging into two live copies.
- Full `agents/scrum_team/tests` suite and top-level `pytest tests/` both pass with no regressions.

## Notes
- `_run`'s new `env_overrides` parameter is generic (merged into the subprocess environment after the
  existing GH_TOKEN/git-identity injection, replacing nothing) - not pytest-specific - so any other
  future subprocess call needing a targeted environment override can reuse it instead of each inventing
  its own mechanism.
- `PYTHONPATH` is deliberately exempted from `tests/test_docker_compose_env.py`'s "every env var the
  agent code reads must be forwarded via docker-compose" completeness check (alongside the existing
  `INTERNAL_STATE_REPO_PATH` exemption) - the read in `_execute_test_suite_coverage` is purely
  defensive (preserve+prepend whatever the container's own process already has), not a host `.env`
  value sourced through Compose at all.
- The max-events cap (`run_eval.py --max-events-per-sprint`, default 300) firing before any nudge or
  grace mechanism ever got a chance is itself a real, distinct risk this run surfaced (a wasteful retry
  loop can burn the *event* budget faster than the *token* budget, with zero grace treatment for events
  the way ISSUE-0046 added for tokens) - not fixed here, since removing the root cause of the loop (this
  issue's Finding 1) addresses the immediate case; flagged as a follow-up if a similar loop resurfaces
  for a different reason.
- Two other reported symptoms (zero merged implementation PRs, no sprint report) are direct downstream
  consequences of Finding 1 consuming the entire event/token budget before any story could reach
  `Tested`/`merge_story_pr` or any close-out sequence could start - not independent bugs.

## Test Approach
- `agents/scrum_team/tests/test_base.py::test_env_overrides_are_applied_to_the_subprocess_environment` -
  `_run`'s new parameter reaches the actual subprocess call.
- `agents/scrum_team/tests/test_quality.py::test_execute_test_suite_coverage_puts_repo_root_on_pythonpath`,
  `test_execute_test_suite_coverage_prepends_to_existing_pythonpath` - the exact env value passed to
  `_run`.
- `agents/scrum_team/tests/test_scrum.py::test_update_roadmap_tags_stories_with_their_version` - an
  end-to-end regression matching the real bug: `update_roadmap` creates a version section referencing an
  existing story, then a simulated later stage-transition sync (`_sync_roadmap_for_story`) must target
  that same section with the real title, not silently diverge.
- Full `agents/scrum_team/tests` suite (via `docker compose --env-file .env.test run --rm --entrypoint ""
  -e PYTHONPATH=/app agent pytest agents/scrum_team/tests`, with `db`/`litellm` up first): 522 passed, no
  regressions.
- `pytest tests/`: 421 passed, no regressions.

## Resolution
- `agents/scrum_team/tools/base.py`: `_run` gains an `env_overrides: dict | None = None` parameter,
  merged into the subprocess environment last.
- `agents/scrum_team/tools/quality.py`: `_execute_test_suite_coverage` passes
  `env_overrides={"PYTHONPATH": <repo_root prepended onto any existing PYTHONPATH>}` to `_run`.
- `agents/scrum_team/tools/requirements.py`: `update_roadmap` sets `product_data["version"] = version`
  whenever a `stories` entry matches an existing `product_backlog` item, in both the
  existing-version-section and new-version-section code paths.
- `tests/test_docker_compose_env.py`: `PYTHONPATH` added to the documented, deliberate
  `_NOT_EXPECTED_FROM_COMPOSE` allowlist.
- `agents/scrum_team/tests/test_story_pipeline_state_machine.py`,
  `agents/scrum_team/tests/test_sprint_and_approval_gates.py`: fake `_run` stand-ins updated to accept
  the new `env_overrides` keyword.
