# Issue

- Issue ID: ISSUE-0035
- Title: Eval Token Budget Too Low, And Confusing Per-Sprint vs Whole-Engagement Budget Naming
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #81): "The Total Token limit for the eval is set too low, so that the last two
sprints are not fully working. Please increase it... I think it would be good if we improve the
naming of the token limit... so it gets clear which limit is for all sprints combined, and which is
per sprint. For local usecases a USD budget does not really make sense, guide the user to set a limit
that suits the actual usecase... Review the whole limit logic and make sure there is no scenario that
can cause unexpected cloud costs."

**Root cause (numeric bug)**: confirmed via `gh run view --log` on a real eval run
(2026-07-29T09:12): sprints 4 and 5 of 5 both hit `🚫 [TOKEN BUDGET EXCEEDED] Sprint token limit
(2,600,000) reached` (actual usage 2,614,205 and 2,797,800) - later, more complex sprints (larger
accumulated backlog/history) genuinely need more tokens than earlier ones, and 2,600,000 didn't have
enough headroom.

**Root cause (naming)**: `SPRINT_TOKEN_BUDGET` and `SPRINT_USD_BUDGET` share the same `SPRINT_` prefix
but have opposite reset semantics - `SPRINT_TOKEN_BUDGET` is genuinely per-sprint (resets
automatically via `reset_sprint_budget`/the eval harness's own per-sprint reset), while
`SPRINT_USD_BUDGET` is a whole-engagement ceiling that deliberately *never* resets (enforced by
LiteLLM's `scrum-sprint-budget` object) - `reset_sprint_budget`'s own docstring already documented
this asymmetry, but the env var name did not reflect it. Same asymmetry existed in the eval-harness
env vars (`EVAL_SPRINT_TOKEN_BUDGET` per-sprint / `EVAL_SPRINT_USD_BUDGET` a per-sprint *rate* scaled
into a whole-run ceiling), and `.env.example`'s own comment describing them was stale/incorrect
(claimed both were "cumulative for the whole run", true only for the USD one).

**Local vs cloud guidance**: `setup_llm.py`'s `prompt_project_settings` asked the same USD-budget
question regardless of provider, even though ISSUE-0033 already established it's a no-op for a
local/Ollama setup.

## Acceptance Criteria
- `EVAL_SPRINT_TOKEN_BUDGET`'s default has real headroom above observed peak per-sprint usage, so a
  standard 5-sprint eval run completes all 5 sprints without a token-budget halt.
- The whole-engagement USD ceiling is renamed to `TOTAL_USD_BUDGET` (from `SPRINT_USD_BUDGET`) so its
  name states its actual scope; the per-sprint token allowance's name is unchanged since it was
  already accurately "per sprint" in behavior. The older name is still honored everywhere it was
  previously read, with a one-time deprecation warning, so an existing `.env` is never silently
  downgraded to a more permissive hardcoded default.
- The eval harness's per-sprint USD *rate* is renamed to `EVAL_USD_BUDGET_PER_SPRINT` (from
  `EVAL_SPRINT_USD_BUDGET`) for the same reason, with the same deprecated-fallback treatment.
- `setup_llm.py`'s local/Ollama flow no longer asks for a USD budget at all (explains why and skips
  it); the cloud flow still asks, now for `TOTAL_USD_BUDGET`.
- Docs (`docs/BUDGET.md`, `MANUAL.md`, `RELEASE.md`, `docs/EVALUATION.md`, `qa/0.1.0-testplan.md`) and
  in-product prompt text (`agents/scrum_team/prompts.py`) reflect the new names and the per-sprint vs
  whole-engagement distinction explicitly.
- The "review the whole limit logic" ask surfaces one genuine remaining gap rather than silently
  leaving it unaddressed: a local/Ollama setup currently has **no cumulative cap across many
  sprints** (only each sprint's token spend is bounded) since `TOTAL_USD_BUDGET` doesn't apply there
  (ISSUE-0033) - documented in `docs/BUDGET.md` and tracked as a follow-up in EP-0009 (not implemented
  in this fix - a new wall-clock or cumulative-token mechanism is its own scoped feature).

## Notes
- Every other env var read site touching this budget was found and fixed, not just
  `check_cost_budget_callback` - `agents/scrum_team/tools/scrum.py`'s `init_scrum_state` and
  `agents/scrum_team/tools/budget.py`'s `create_litellm_virtual_key` each had their own independent
  `os.environ.get("SPRINT_USD_BUDGET", ...)` fallback that needed the same treatment.
- `agents/scrum_team/helpers.py`'s new `get_env_with_deprecated_fallback(new_name, old_name)` is the
  shared mechanism for this and any future rename - warns once per process (not once per call, since
  `check_cost_budget_callback` runs on every single turn).
- Deliberately does **not** rename `SPRINT_TOKEN_BUDGET` - unlike the USD var, its name already
  accurately describes its per-sprint scope; renaming it would have widened this change's blast
  radius without fixing an actual point of confusion.
- `.env.test` deliberately keeps the deprecated names (`SPRINT_USD_BUDGET`/`EVAL_SPRINT_USD_BUDGET`)
  rather than being updated to the new ones - this doubles as a live regression check that the
  container test suite still works correctly via the fallback path.

## Test Approach
- `agents/scrum_team/tests/test_helpers.py::TestGetEnvWithDeprecatedFallback` - prefers the new name,
  falls back to the old one, returns `None` if neither set, warns exactly once per process.
- `agents/scrum_team/tests/test_agent.py` - `check_cost_budget_callback` enforces the deprecated
  `SPRINT_USD_BUDGET` value when set (not the 10.0 default), and prefers `TOTAL_USD_BUDGET` when both
  are set.
- `agents/scrum_team/tests/test_scrum.py` - same two cases for `init_scrum_state`.
- `agents/scrum_team/tests/test_budget_api.py` - same fallback case for `create_litellm_virtual_key`.
- `tests/test_setup_llm.py::TestPromptProjectSettings` - cloud flow prompts for and writes
  `TOTAL_USD_BUDGET`, clearing the stale old-name line; local flow never prompts for it at all
  (would raise `StopIteration` if it tried) while still writing a harmless default.
- Full `agents/scrum_team/tests` suite (via `docker compose --env-file .env.test run --rm
  --entrypoint "" -e PYTHONPATH=/app agent pytest --cov=agents agents/scrum_team/tests`): 248 passed,
  no regressions.
- `pytest tests/`: 288 passed, no regressions (includes `test_docker_compose_env.py`, which confirms
  `TOTAL_USD_BUDGET`/`EVAL_USD_BUDGET_PER_SPRINT` are present in both compose files' agent
  environment).

## Resolution
- `agents/scrum_team/helpers.py`: new `get_env_with_deprecated_fallback()`.
- `agents/scrum_team/agent.py`, `tools/scrum.py`, `tools/budget.py`: all three USD-budget env-var
  read sites now use it (`TOTAL_USD_BUDGET` preferred, `SPRINT_USD_BUDGET` deprecated fallback).
- `agents/scrum_team/scripts/run_eval.py`: `EVAL_USD_BUDGET_PER_SPRINT` (preferred) /
  `EVAL_SPRINT_USD_BUDGET` (deprecated fallback) for the per-sprint USD rate; sets the resolved
  whole-run ceiling into `TOTAL_USD_BUDGET`, not the old name.
- `.env.example` / `.env.local.example`: `EVAL_SPRINT_TOKEN_BUDGET` raised 2,600,000 -> 4,000,000;
  `TOTAL_USD_BUDGET`/`EVAL_USD_BUDGET_PER_SPRINT` introduced with clarifying comments on scope.
- `.github/workflows/eval.yml`: mirrors the same default bump and rename.
- `docker-compose.yaml` / `docker-compose.local.yaml`: pass through both the new and deprecated names.
- `setup_llm.py`: `prompt_project_settings(env_path, is_local=False)` - skips the USD-budget question
  entirely for the local/Ollama flow; cloud flow now prompts for/writes `TOTAL_USD_BUDGET` and clears
  any stale `SPRINT_USD_BUDGET` line.
- `agents/scrum_team/prompts.py`: model-facing env-var names updated to match.
- `docs/BUDGET.md`, `MANUAL.md`, `RELEASE.md`, `docs/EVALUATION.md`, `qa/0.1.0-testplan.md`: updated
  for the new names and the per-sprint/whole-engagement distinction; `docs/BUDGET.md` also documents
  the local-mode cumulative-cap gap and links [EP-0009](../stories/EP-0009-Time-And-Throughput-Based-Sprint-Limits-For-Local-Setups.md).
