# Issue

- Issue ID: ISSUE-0030
- Title: Config Never Loaded Unless the Orchestrator Proactively Called init_scrum_state()
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #72): starting a fresh ADK web session, all config in the state repo showed as
undefined, no proactive greeting (ISSUE-0027) was posted, and the interaction stopped entirely -
possibly caused by budgets showing as 0. The reporter's own suggested fix: "allow passing in of
config, or create a tool, where the agent can access all relevant config" (explicitly not secrets).

**Root cause**: `init_scrum_state()` - the tool that actually reads `GITHUB_REPO_URL`,
`SPRINT_TOKEN_BUDGET`, `SPRINT_USD_BUDGET`, `INTERACTION_LEVEL`, etc. from the environment into
`session.state` - was only ever called by the Orchestrator *choosing* to call it as a tool. Nothing
mechanically guaranteed it ran. If the model never called it (a real, demonstrated failure mode - see
ISSUE-0029/GH issue #70, a separate session that got stuck never calling any tool at all),
`state.repo`/`state.budgets` stayed at their all-empty/all-zero `ScrumState` defaults regardless of
what was actually configured in `.env` - explaining "config in the state repo is undefined."

Worse, this cascades into a hard failure: `check_cost_budget_callback` runs immediately after in the
`before_model_callback` chain, and ADK's own callback loop (`google.adk.flows.llm_flows.
base_llm_flow._handle_before_model_callback`) stops at the first callback that returns a non-None
response - meaning if `check_cost_budget_callback` short-circuits with a canned response, no later
callback (`sprint_status_injection_callback`, `history_management_callback`) ever runs, *and the
model itself is never invoked*. That callback's own USD-budget check falls back to
`float(os.environ.get("SPRINT_USD_BUDGET", 10.0))` when `state.budgets.total_usd <= 0` - if that env
var is explicitly `"0"` (not merely unset), the fallback resolves to `0.0` too, and the callback halts
the *entire first turn* with a canned "`❌ [CONFIGURATION ERROR] No USD budget limit set`" response
instead of ever reaching the model - matching "no start dialog is posted... the interaction stops...
may be caused by budgets set to 0" precisely. `init_scrum_state()` already has its own "HARD
GUARDRAIL" that replaces a 0/negative budget with a sane default (1M tokens / $10) -
`agents/scrum_team/tools/scrum.py` - but only once it's actually run, which (per the above) it never
reliably was.

## Acceptance Criteria
- Config (repo URL, budgets, interaction level, GitHub App credentials) is loaded from the
  environment/state repo mechanically, before the model's very first turn each session - not
  contingent on the Orchestrator choosing to call `init_scrum_state()` itself.
- This must run *before* `check_cost_budget_callback`, so a misconfigured/explicit-zero
  `SPRINT_USD_BUDGET`/`SPRINT_TOKEN_BUDGET` can no longer silently halt the very first turn before
  `init_scrum_state()`'s own guardrail ever gets a chance to replace it with a sane default.
- Runs at most once per session (not on every single turn) - a persistently-failing call (e.g.
  GitHub App auth down) must not be retried forever, and normal repeated config reads/GitHub App
  token minting shouldn't happen redundantly on every message.
- A failure during this automatic init must not crash the callback chain or the session - it's
  best-effort, same as the rest of this file's defensive callback error handling.
- No new secrets are exposed anywhere - this only guarantees the *existing* `init_scrum_state()` tool
  (which already deliberately excludes `github_token`/`litellm_keys` from the persisted state.json
  export) actually runs; it doesn't change what it reads or where that ends up.

## Notes
- The reporter's two suggested approaches ("allow passing in of config" vs. "create a tool") both
  already exist in spirit - `init_scrum_state()` already reads all relevant config from `.env`/the
  state repo. The actual gap was that nothing *guaranteed* it ran, which is what this change fixes,
  rather than adding a second, parallel config-reading mechanism.
- This is a distinct root cause from ISSUE-0029/GH issue #70 (the conversation-history duplication
  bug), though both manifest as "the Orchestrator seems stuck/unresponsive." ISSUE-0029 explains a
  session that *did* get the model running but confused by a bloated context; this issue explains a
  session where the model may never even have been invoked at all, because a budget check hard-halted
  before it ever got the chance.

## Test Approach
- `agents/scrum_team/tests/test_agent.py::TestEnsureStateInitializedCallback` - calls
  `init_scrum_state(tool_context=callback_context)` on first use; does not call it again once the
  `_state_auto_initialized` flag is set; skipped entirely for specialist agents; a failure inside
  `init_scrum_state()` doesn't raise and still sets the flag (so it isn't retried every turn); the
  callback is registered *first* in `root_agent.before_model_callback`, ahead of
  `check_cost_budget_callback`.
- Full `agents/scrum_team/tests` suite (via `docker compose --env-file .env.test run --rm
  --entrypoint "" -e PYTHONPATH=/app agent pytest --cov=agents agents/scrum_team/tests`): 226 passed,
  no regressions.
- `pytest tests/` (host-side suite, unaffected): 274 passed.

## Resolution
- `agents/scrum_team/agent.py`: new `ensure_state_initialized_callback()`, registered first (before
  `inject_litellm_key_callback`/`check_cost_budget_callback`) in `root_agent`'s
  `before_model_callback` list. Calls `init_scrum_state(tool_context=callback_context)` once per
  session (guarded by a `_state_auto_initialized` state flag), swallowing any exception.
- `agents/scrum_team/prompts.py`: `ORCHESTRATOR_PROMPT`'s OPERATING STYLE updated to reflect that
  state is now initialized automatically, while still instructing the model to call
  `init_scrum_state()` itself again after anything that changes config mid-session.
- `MANUAL.md` § Troubleshooting: documents the fix and the explicit-zero-budget gotcha for end users.
