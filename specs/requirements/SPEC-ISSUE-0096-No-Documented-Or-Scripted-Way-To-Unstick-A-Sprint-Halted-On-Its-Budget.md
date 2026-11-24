# Issue

- Issue ID: SPEC-ISSUE-0096
- Title: No Documented Or Scripted Way To Unstick A Sprint Halted On Its Budget - Usage Lives In Two Stores, Only start_sprint Ever Resets It, And The Halt Never Names The Override
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-11-24

## Overview
A user report says the documentation doesn't give the actual call to reset or override the budget when
a project is stuck. SPEC-ISSUE-0092 (#557) added `python3 run.py override-budget --tokens N [--usd N]
--reason "..."`, a live, sprint-scoped raise of the limit. But that call reaches neither the person
staring at the halt nor most of the docs, and several real recovery cases still have no supported path:

1. **The halt message never names the override.** The `[TOKEN BUDGET EXCEEDED]` and
   `[USD BUDGET EXCEEDED]` `detail` texts in `check_cost_budget_callback`
   (`agents/scrum_team/agent.py`) still said only *"raise SPRINT_TOKEN_BUDGET in .env and restart the
   agent container"*. Those texts are what `_notify_critical_halt` records as the blocking interaction
   and sends to every notifier. `override-budget` appeared only in `docs/BUDGET.md` and in
   `run.py --help`. `TROUBLESHOOTING.md`, `MANUAL.md` §5 and `README.md` never mentioned it.
2. **Nothing resets usage outside `start_sprint`.** `override-budget` and `.env` both raise the
   *limit*. `token_usage.total` is only ever cleared by `sprint_budget_reset_state_delta()`
   (`agents/scrum_team/tools/budget.py`), whose only caller is `start_sprint`; GH #413 deliberately
   removed the agent-invoked `reset_sprint_budget` tool. Giving the current sprint a fresh allowance
   meant raising the ceiling above whatever had already been burned.
3. **Usage is persisted in two places, and editing one doesn't stick.** `token_usage` is one of
   `REPO_STATE_KEYS` and goes to `<STATE_REPO_PATH>/.hc/state.json`, which a new session loads on its
   first turn (`ensure_state_initialized_callback` -> `load_state_from_repo`). It also lives in ADK's
   sqlite session store, `sessions/adk_sessions.db` (`--session_service_uri` in
   `agents/scrum_team/scripts/run_agent.sh`), as a resumed session's own copy. That copy overwrites
   `state.json` again on its next `save_state_to_repo`. Observed on a real installation while writing
   this spec: the state repo showed sprint 0 with 0 tokens used, while the most recent stored session
   showed sprint 4 with 840,665.
4. **An override can't be undone early, and it silently masks `.env`.** While `budget_human_override`
   pins a key, `sync_budgets_from_env` (`agents/scrum_team/tools/scrum.py`) skips `.env` for it. A
   human who overrides and then decides to fix `.env` properly gets the override value for the rest of
   the sprint, with nothing indicating why.
5. **The USD ceiling has a second copy in LiteLLM.** `override-budget --usd` and `TOTAL_USD_BUDGET`
   only feed `budgets.total_usd`. LiteLLM's own `scrum-sprint-budget` `max_budget` is updated only
   inside `create_litellm_virtual_key`, which runs when a role's key is created.

## Acceptance Criteria
- A host-side, stdlib-only `reset_budget.py`, the offline counterpart to `override-budget`:
  - Read-only by default: shows limits, usage in both stores, open budget halts and any override pin.
  - `--reset-usage` clears the budget keys `start_sprint` clears, in both `state.json` (with a local
    git checkpoint) and the most recent ADK session (or `--session-id` / `--all-sessions`).
  - `--token-budget` / `--usd-budget` raise the limits in `.env` and in state, and unpin that key from
    `budget_human_override` so the new value applies. `--usd-budget` also updates LiteLLM's
    `scrum-sprint-budget` (best-effort `POST /budget/update`).
  - `--clear-override` removes an override pin early.
  - Asks for confirmation (`--yes` skips it) and refuses while the agent container is running
    (`--force` overrides). Never resets LiteLLM's recorded USD spend.
- The script's reset key list can't silently drift from `sprint_budget_reset_state_delta()`, the
  SPEC-ISSUE-0049 failure mode. A test pins every key and value to that function.
- The halt `detail` texts name the actual commands: `override-budget`, `reset_budget.py`, and the
  docs section.
- `docs/BUDGET.md` has one recovery section that merges SPEC-ISSUE-0092's: a decision table, both
  tools, and the manual equivalent with the exact LiteLLM API call. `TROUBLESHOOTING.md`, `MANUAL.md`
  §5, `README.md` and `docs/SETUP.md` point to it.

## Notes
- No guardrail or budget *logic* changes. `check_cost_budget_callback` enforcement,
  `sync_budgets_from_env` and `sprint_budget_reset_state_delta` are untouched; only the halt's
  human-facing `detail` strings change. `detail` goes to `_notify_critical_halt` (blocking interaction
  plus notifiers), never into the model's response text, so it can't affect agent trajectories. This
  still touches the budget mechanism's operating procedure, so per AGENTS.md §12 the PR asks for
  explicit human sign-off.
- `--reset-usage` deliberately does **not** clear `sprint_report`, `transcript_path` etc., which are
  also in `sprint_budget_reset_state_delta()`. Those belong to the real sprint boundary.
- Related: SPEC-ISSUE-0092 / #557 (live override), GH #413 (mechanical reset via `start_sprint`),
  GH #220 (budget defaults and grace), SPEC-ISSUE-0049 (reset-list drift).

## Test Approach
- `tests/test_reset_budget.py` (host suite, no Docker):
  - An AST-based drift guard against `sprint_budget_reset_state_delta()`.
  - Pure-function tests for the reset, limit, override-unpin and halt-resolution logic.
  - End-to-end runs against a fixture install (a git state repo plus a real sqlite store with two
    sessions) for status (read-only, shows the override pin), `--reset-usage` (only the newest session;
    `.env` untouched), `--all-sessions --token-budget`, `--usd-budget` (LiteLLM call mocked) and
    `--clear-override`, and the refusal paths: agent running, non-positive budget, unknown session,
    declined prompt.

## Resolution
Implemented as described: `reset_budget.py`, `tests/test_reset_budget.py`, updated halt `detail` texts in
`agents/scrum_team/agent.py`, and `docs/BUDGET.md` "Recovering From A Stuck Budget Halt", merged with
SPEC-ISSUE-0092's existing section into one decision table (live `override-budget` vs offline
`reset_budget.py`), with pointers from the other docs.
