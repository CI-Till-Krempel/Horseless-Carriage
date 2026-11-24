# Issue

- Issue ID: SPEC-ISSUE-0096
- Title: No Documented Or Scripted Way To Unstick A Sprint Halted On Its Budget - Usage Lives In Two Stores And Only start_sprint Ever Resets It
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-11-24

## Overview
A user report says the documentation doesn't give the actual call that resets or overrides the budget
when a project is stuck. That is accurate. Once a sprint halts on `🚫 [TOKEN BUDGET EXCEEDED]`, the
only guidance anywhere is the halt's own `detail` text (`agents/scrum_team/agent.py`, inside
`check_cost_budget_callback`): *"To resume immediately: raise SPRINT_TOKEN_BUDGET in .env and restart
the agent container."* That advice is incomplete in three concrete ways:

1. **It only covers the limit, not the usage.** Raising `SPRINT_TOKEN_BUDGET` works for the ceiling,
   because `sync_budgets_from_env` (`agents/scrum_team/tools/scrum.py`) copies it into `budgets.total`
   on every turn. But `token_usage.total` is only ever cleared by `sprint_budget_reset_state_delta()`
   (`agents/scrum_team/tools/budget.py`), and the only caller of that is `start_sprint`. GH #413
   deliberately removed the agent-invoked `reset_sprint_budget` tool. So there is no supported way to
   give the *current* sprint a fresh allowance short of raising the ceiling above whatever has already
   been burned.
2. **Usage is persisted in two places, and editing one doesn't stick.** `token_usage` is one of
   `REPO_STATE_KEYS` and goes to `<STATE_REPO_PATH>/.hc/state.json`, which a new session loads on its
   first turn (`ensure_state_initialized_callback` -> `load_state_from_repo`). It also lives in ADK's
   sqlite session store, `sessions/adk_sessions.db` (`--session_service_uri sqlite:///...` in
   `agents/scrum_team/scripts/run_agent.sh`), as a resumed session's own copy. That copy overwrites
   `state.json` again on its next `save_state_to_repo`. A hand edit of `state.json` with a resumed
   session is silently undone. Observed on a real installation while writing this spec: the state repo
   showed sprint 0 with 0 tokens used, while the most recent stored ADK session showed sprint 4 with
   840,665 tokens used. The two stores really do diverge.
3. **The USD ceiling has a second copy in LiteLLM.** `TOTAL_USD_BUDGET` feeds `budgets.total_usd`
   every turn. LiteLLM's own `scrum-sprint-budget` `max_budget` is only updated from inside
   `create_litellm_virtual_key`, which runs when a role's key is created. The USD halt text mentions
   the dashboard but no API call.

## Acceptance Criteria
- A host-side, stdlib-only `reset_budget.py`:
  - Read-only by default: shows the effective limits, usage in both stores and open budget halts.
  - `--reset-usage` clears the budget keys `start_sprint` clears, in both `state.json` (with a local
    git checkpoint) and the most recent ADK session (or `--session-id` / `--all-sessions`).
  - `--token-budget` / `--usd-budget` raise the limits in `.env` and in state. `--usd-budget` also
    updates LiteLLM's `scrum-sprint-budget` (best-effort `POST /budget/update`).
  - Asks for confirmation (`--yes` skips it) and refuses while the agent container is running
    (`--force` overrides).
  - Never resets LiteLLM's recorded USD spend: that is a whole-engagement record, and the honest knob
    is raising the ceiling.
- The script's reset key list can't silently drift from `sprint_budget_reset_state_delta()`, the
  SPEC-ISSUE-0049 failure mode. A test pins every key and value to that function.
- `docs/BUDGET.md` documents the recovery procedure, including the manual equivalent with the exact
  LiteLLM API call. `TROUBLESHOOTING.md`, `MANUAL.md` §5, `README.md` and `docs/SETUP.md` point to it.

## Notes
- This changes no guardrail or budget logic in `agents/scrum_team/`; it only gives a human operator a
  safe, explicit way to apply the knobs `docs/BUDGET.md` already describes. It touches the budget
  *mechanism's* operating procedure, though, so per AGENTS.md §12 the PR asks for explicit human
  sign-off rather than assuming it.
- Deliberately does **not** clear `sprint_report`, `transcript_path` etc., which are also in
  `sprint_budget_reset_state_delta()`. Those belong to the real sprint boundary, not to a mid-sprint
  budget repair.
- Possible follow-up (out of scope here, since it would put this PR on the ADK eval gate): have the
  `[TOKEN BUDGET EXCEEDED]` / `[USD BUDGET EXCEEDED]` `detail` texts in `agent.py` mention
  `reset_budget.py` directly.
- Related: GH #413 (reset made purely mechanical via `start_sprint`), GH #220 (budget defaults/grace),
  SPEC-ISSUE-0049 (reset-list drift).

## Test Approach
- `tests/test_reset_budget.py` (host suite, no Docker):
  - An AST-based drift guard against `sprint_budget_reset_state_delta()`.
  - Pure-function tests for the reset, limit and halt-resolution logic.
  - End-to-end runs against a fixture install (a git state repo plus a real sqlite store with two
    sessions) for status (read-only), `--reset-usage` (only the newest session; `.env` untouched),
    `--all-sessions --token-budget`, `--usd-budget` (LiteLLM call mocked), and the refusal paths:
    agent running, non-positive budget, unknown session, declined prompt.

## Resolution
Implemented as described: `reset_budget.py`, `tests/test_reset_budget.py`, and a new
`docs/BUDGET.md` § 3 "Recovering a sprint stuck on its budget", with pointers from the other docs.
