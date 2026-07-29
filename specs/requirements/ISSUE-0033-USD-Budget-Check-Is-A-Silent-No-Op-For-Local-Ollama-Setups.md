# Issue

- Issue ID: ISSUE-0033
- Title: USD Budget Check Is A Silent No-Op For Local/Ollama Setups
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #75): "Is Ollama configured to update to the newest pricing info?"

**Investigation**: this repo never computes cost itself - the USD budget guardrail
(`check_cost_budget_callback` step 2, `agents/scrum_team/agent.py`) trusts the LiteLLM proxy's own
`spend` figure, which LiteLLM derives internally from its bundled `model_prices_and_context_window.json`
cost map. Neither `litellm.yaml` nor either `docker-compose*.yaml` pins or disables LiteLLM's own
startup fetch of that cost map, so whatever LiteLLM's default does there is unaffected by this repo -
there was no actual "stale pricing" bug to fix for cloud providers.

However, for the fully-local Ollama path
(`config/model-templates/litellm.local-ollama.yaml`), a self-hosted model has no real per-token API
charge, and LiteLLM's cost map has no meaningful pricing entry for an arbitrary custom Ollama tag like
`ollama/llama3.1:8b`. That means `response_cost`/`spend` for every call in a local sprint is
effectively `$0.00` regardless of actual token usage - so `check_cost_budget_callback`'s
`if current_spend >= budget_limit` check can never fire. The USD guardrail silently "passes" forever
in local mode, giving the impression a budget is being enforced when it isn't; only the local **token**
budget was ever meaningfully capping a local/Ollama sprint, and this wasn't documented anywhere.

## Acceptance Criteria
- The USD budget check is skipped outright (not run, not silently-always-passing) when running
  against a local/self-hosted provider, rather than making a network round-trip to LiteLLM for a
  number that can never meaningfully exceed the budget.
- The token budget continues to apply unchanged and remains the real guardrail for a local sprint.
- This is documented in `docs/BUDGET.md` and `MANUAL.md` so a user configuring `SPRINT_USD_BUDGET` for
  a local/Ollama setup understands it has no effect there.
- No behavior change for cloud-provider (proxy) setups.

## Notes
- Detection: `docker-compose.local.yaml` now sets `LLM_LOCAL_PROVIDER=true` on the `agent` service
  (the one docker-compose file used for the local/Ollama stack, per `lib_docker.compose_file_args`);
  `docker-compose.yaml` sets it to `false`. This is a static, compose-file-level distinction - the
  agent container itself never needs to introspect which model backend it's talking to.
- This is a distinct issue from ISSUE-0030/GH issue #72 (budget defaults of `0` hard-halting the very
  first turn) - that one was about a misconfigured *value*; this one is about the USD check being
  structurally meaningless for a whole class of setup regardless of value.

## Test Approach
- `agents/scrum_team/tests/test_agent.py::TestAgent::test_check_cost_budget_callback_skips_usd_check_for_local_provider` -
  with `LLM_LOCAL_PROVIDER=true`, `check_cost_budget_callback` returns `None` without calling
  `requests.post` at all, and records a one-time notice flag in state.
- Full `agents/scrum_team/tests` suite (via `docker compose --env-file .env.test run --rm
  --entrypoint "" -e PYTHONPATH=/app agent pytest --cov=agents agents/scrum_team/tests`): 234 passed,
  no regressions.
- `pytest tests/` (host-side suite): 281 passed, no regressions.

## Resolution
- `docker-compose.yaml` / `docker-compose.local.yaml`: added `LLM_LOCAL_PROVIDER=false` /
  `LLM_LOCAL_PROVIDER=true` respectively to the `agent` service's `environment:`.
- `agents/scrum_team/agent.py`: `check_cost_budget_callback` step 2 now returns `None` immediately
  (after a one-time informational log) when `LLM_LOCAL_PROVIDER=true`, skipping both the budget-limit
  resolution and the proxy round-trip.
- `docs/BUDGET.md` and `MANUAL.md` § Budgets: documented that the USD budget doesn't apply to a
  local/Ollama setup and that the token budget is the real guardrail there.
