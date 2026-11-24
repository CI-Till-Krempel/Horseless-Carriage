# Issue

- Issue ID: SPEC-ISSUE-0092
- Title: A Sprint Halted By The Token/USD Budget Has No Live Recovery Path - Only .env + Restart
- Status: Done
- Priority: Should
- Owner: Till Krempel
- Last Updated: 2026-11-24

## Overview
A real, production (not eval) run of Horseless Carriage got stuck: the agent container answered
every turn with the mechanical `🚫 [TOKEN BUDGET EXCEEDED]` halt from `check_cost_budget_callback`
(`agents/scrum_team/agent.py:1157-1169`) and nothing the operator typed into the running session
could unstick it. The halt's own `detail` text names the only documented recovery:

> "To resume immediately: raise SPRINT_TOKEN_BUDGET in .env and restart the agent container."

That's a heavyweight round-trip (edit `.env`, `docker compose up` again) for "let this one sprint
keep going right now," and it's the *only* option - there is no live, in-session way for a human to
say "let this turn through" once this path trips.

**Confirmed mechanism.** `check_cost_budget_callback` is registered as a `BeforeModelCallback`
(`agent.py:993`) - it synthesizes an `LlmResponse` and returns before the LLM is ever invoked
(`agent.py:1169`, via `_budget_halt_response`, `agent.py:777-847`). Because of that, **no chat
message or tool call from a human can reach a live model turn to resolve it** - the existing
human-in-the-loop mechanism (`record_blocking_interaction`/`resolve_blocking_interaction`,
`agents/scrum_team/tools/notifications.py:85,129`) is itself an agent-invoked tool, so it's exactly
as unreachable as everything else once this halt is active. The budget ceiling that trips it,
`state.budgets.total`/`total_usd`, is re-synced from `SPRINT_TOKEN_BUDGET`/`TOTAL_USD_BUDGET`
**unconditionally on every single turn** by `sync_budgets_from_env`
(`agents/scrum_team/tools/scrum.py:104-158`) - itself a deliberate anti-staleness fix for a prior
incident (that function's own docstring: a sprint halted at a token limit of 100 while `.env`'s real
value was 6,000,000 the whole time). That means even a direct, out-of-band write to
`state.budgets.total` on the live ADK session would be silently undone on the very next turn, unless
`sync_budgets_from_env` itself is taught to leave a human-set value alone.

## Acceptance Criteria
- A human can raise the current sprint's token and/or USD budget ceiling on an **already-running**
  agent container, with no `.env` edit and no container restart, and have it take effect on the very
  next turn.
- The override must not be silently discarded by `sync_budgets_from_env`'s per-turn env resync.
- The override must **not** become a new, permanent staleness trap the way the incident
  `sync_budgets_from_env`'s own docstring already describes: it is scoped to exactly the sprint it
  was applied during, and is cleared automatically the moment the next sprint starts
  (`sprint_budget_reset_state_delta`, `agents/scrum_team/tools/budget.py`), so it can never mask a
  later, real `.env` fix + restart.
- The override is auditable: it's recorded in `blocking_interactions`
  (`agents/scrum_team/tools/notifications.py`), the same place every other human-facing incident
  already shows up, with the human-supplied reason attached.
- A test that constructs the exact stuck-sprint shape (env says a low ceiling, a human override pins
  a higher one) and asserts `sync_budgets_from_env` leaves the override standing - this is the test
  that fails today (pre-fix) and passes once fixed.

## Notes
- **This is a budget-mechanism change under `AGENTS.md` Section 12** ("Guardrails and budget
  mechanisms require human confirmation") - it adds a human-triggered escape hatch to
  `check_cost_budget_callback`'s enforcement path (via `sync_budgets_from_env`, one of the files that
  section names explicitly). Per that section: "do not land a change that loosens, removes, or
  silently reinterprets a ... budget limit without explicit human sign-off in the PR itself." This
  spec-issue and its implementation PR are the proposal; the PR itself still needs a reviewer's
  one-line "approved by `<name>`" sign-off comment before merge - it is not implied by this doc nor
  by the PR being opened.
- The override does not change `check_cost_budget_callback`'s enforcement logic itself, nor loosen it
  for anyone who hasn't explicitly invoked the override - it only adds an operator-initiated,
  audited, sprint-scoped path to raise the ceiling the exact same callback already reads live every
  turn (`state.budgets.total`/`total_usd`).
- See `docs/BUDGET.md` for the existing two-layer budget scheme this slots into.

## Test Approach
- `agents/scrum_team/tests/test_scrum.py::TestSyncBudgetsFromEnv` - override pins one/both keys,
  env resync must not clobber the pinned key(s), and must still sync the non-pinned key normally.
- `agents/scrum_team/tests/test_budget.py` - `sprint_budget_reset_state_delta` clears the override.
- `tests/test_run.py::TestOverrideBudgetDispatch` - the new `python3 run.py override-budget` CLI
  dispatch builds the right `docker compose exec` invocation and requires `--tokens`/`--usd`.
