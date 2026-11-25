# Issue

- Issue ID: SPEC-ISSUE-0098
- Title: Hitting TOTAL_USD_BUDGET leaves the active sprint unrecoverable and impossible to close
- Status: Done
- Priority: Must
- Owner: Horseless Carriage maintainers
- Last Updated: 2026-11-25

## Overview

GH issue #563: once a project hits the whole-engagement USD cap (`TOTAL_USD_BUDGET`, enforced both
in-process by `check_cost_budget_callback` and by the LiteLLM proxy's `scrum-sprint-budget`), the
active sprint can't be closed out. The only recovery the reporter found was to discard
`.hc/state.json`, which also threw away the last sprint's results. Hitting the *token* budget
(`SPRINT_TOKEN_BUDGET`) closes the sprint out gracefully. The USD cap should do the same.

The token path has three close-out mechanisms. The USD path was missing all three:

1. **Scrum Master had no USD grace.** On the token path, GH #395 gave Scrum Master its own ritual
   budget, which the main ceiling doesn't touch (`agent.py`, `if agent_name == "ScrumMaster":`,
   `origin/main` `agents/scrum_team/agent.py:1060`). The USD check had no such split. It admitted
   only `SPRINT_CLOSEOUT_GRACE_ROLES` = `{"ProductOwner", "ScrumOrchestrator"}`
   (`helpers.py:220`) to its grace window:

   ```python
   # origin/main agents/scrum_team/agent.py:1286
   if agent_name in SPRINT_CLOSEOUT_GRACE_ROLES and current_spend < grace_usd_limit:
       pass
   ```

   So the moment spend crossed the cap, Scrum Master was hard-halted with `🚫 [USD BUDGET EXCEEDED]`.
   It is the only role that can run the retrospective, `calculate_kpis` and `create_sprint_report`.
   Worse, `_budget_halt_response` redirected every non-grace role to ProductOwner with text
   instructing it to "transfer to Scrum Master for the retrospective, KPIs, and the sprint report
   ... on its own separate ritual budget that this main-budget exhaustion does not affect"
   (`origin/main` `agent.py:839`). That is true for tokens and false for USD. ProductOwner and Scrum
   Master just bounced the turn back and forth, and each hop spent ProductOwner's grace.

2. **Past the USD grace ceiling, the mechanical safety net could fail to fire.**
   `_ensure_sprint_report_on_final_halt_once` (fallback sprint report, live state/roadmap commit,
   release PR, all with zero LLM spend) was only ever called from `_budget_halt_response`'s
   grace-eligible branch (`origin/main` `agent.py:857`). A non-grace role was always redirected to
   ProductOwner instead (`agent.py:831`). USD spend is one shared figure, so once it is past
   `budget * (1 + grace%)` *no* role can make another call. The redirect hands off to an agent that
   is just as frozen. The close-out happened only if ProductOwner itself was re-invoked and halted.
   A repeated-transfer loop breaker, an interactive human, or the eval harness's own retry ceiling
   could each end the turn chain first.

3. **A LiteLLM-proxy-side budget rejection triggered no close-out at all.** The proxy enforces
   `max_budget` itself. A call that the in-process check let through (inside the grace window, or
   with a lagging `/key/info` spend figure) comes back as `litellm.RateLimitError`
   (`Budget has been exceeded! Current cost: ..., Max budget: ...`).
   `_patched_adk_acompletion` (`origin/main` `agent.py:221`) turned that into a plain
   `⚠️ [BUDGET LIMIT]` text reply, and nothing else happened: no roadmap sync, no fallback report, no
   state commit, no release PR. Every later turn got the same rejection.

Together these leave the sprint stuck. Nobody can author the report or open the release PR, and
neither mechanical path is guaranteed to run. The sprint's state never reaches develop through the
safety net's `_rewrite_live_state_into_working_tree` + push. Raising the cap only lets the stuck
sprint resume. It doesn't recover the results of the sprint that was cut off.

## Acceptance Criteria

- Scrum Master can still run its close-out work (retro, KPIs, `create_sprint_report`) after
  `TOTAL_USD_BUDGET` is exhausted. It is bounded by the **same** existing USD grace ceiling
  (`budget * (1 + closeout_grace_percent(state)/100)`), not a new or higher one, and only while its
  sprint report is still outstanding (`sprint_report_pending_release` not yet set).
- DevTeam/QA/Architect still hard-halt the instant the USD cap is reached (unchanged).
- Once USD spend passes the grace ceiling, **whichever role is active** runs the mechanical
  close-out (fallback report + live state/roadmap commit + release PR) exactly once per sprint,
  instead of redirecting to an equally frozen ProductOwner.
- A LiteLLM-proxy budget-exceeded rejection runs the same mechanical close-out once per sprint. An
  ordinary, transient TPM/RPM rate limit does not.
- The USD ceiling is never loosened. `TOTAL_USD_BUDGET`, the LiteLLM `max_budget`, and the grace
  percentage are all unchanged.
- Tests that fail before and pass after: `TestUsdBudgetGracefulCloseout` and
  `TestLiteLlmBudgetRejectionCloseout` in `agents/scrum_team/tests/test_agent.py`.

## Notes

- GH issue #563 is the user report this spec-issue tracks.
- **Budget mechanism, AGENTS.md §12:** this changes `check_cost_budget_callback`'s USD branch and
  needs explicit human sign-off in the PR. It tightens rather than loosens anything: no ceiling moves,
  Scrum Master shares the same grace ceiling ProductOwner already had, and the new final-halt path
  spends *zero* further LLM tokens/USD.
- Builds on SPEC-ISSUE-0092 (PR #557, `run.py override-budget`) and is complementary to
  SPEC-ISSUE-0096 (PR #561, `reset_budget.py` + recovery docs, open at the time of writing).
  Those two are *human* recovery paths for a sprint already stuck on a halt. This spec makes the USD
  halt close the sprint out on its own first, so the sprint's results are already committed before a
  human raises the cap. PR #561 also edits the USD halt's `detail` text in the same function, so
  whichever lands second needs a small textual merge.
- Related: GH #395 (Scrum Master's ritual budget, the token-path counterpart of fix 1),
  SPEC-ISSUE-0045/0046 (non-grace redirect, grace sizing), GH #379 (safety net opens the release
  PR), SPEC-ISSUE-0094 (safety net writes live state to develop).

## Resolution

- `agents/scrum_team/helpers.py`: new `usd_closeout_grace_applies(agent_name, state)`. It returns
  `SPRINT_CLOSEOUT_GRACE_ROLES` plus `ScrumMaster` while `sprint_report_pending_release` is unset.
- `agents/scrum_team/agent.py` `check_cost_budget_callback` USD branch has three outcomes. Inside
  the grace window, `usd_closeout_grace_applies` roles get a real call. Inside the window, other
  roles get the existing halt/redirect. Past the grace ceiling, any role gets
  `_budget_halt_response(..., final=True)`, which fires `_ensure_sprint_report_on_final_halt_once`
  and synthesizes no transfer.
- `agents/scrum_team/agent.py`: `_patched_adk_acompletion` appends `LITELLM_BUDGET_EXCEEDED_MARKER`
  to the degraded reply when `_is_litellm_budget_exceeded_error(e)`. The new after-model callback
  `closeout_on_litellm_budget_rejection_callback` is registered on every agent. It sees the marker
  and runs the roadmap sync, the critical-halt notification and the mechanical close-out, each once
  per sprint.
- The token path is untouched.

## Test Approach

- `TestUsdBudgetGracefulCloseout` covers:
  - Scrum Master gets a real call inside the USD grace window.
  - Scrum Master halts once its report exists.
  - Scrum Master halts past the unchanged ceiling.
  - DevTeam/QA/Architect still halt inside the window.
  - Every role past the ceiling fires the safety net exactly once, with no transfer.
  - The token-path redirect regression guard still holds.
- `TestLiteLlmBudgetRejectionCloseout` covers:
  - A budget-exceeded `RateLimitError` is tagged; a plain rate limit is not.
  - The after-model callback closes out once on a tagged reply and ignores untagged ones.
  - The callback is registered on every agent.
- No ADK evalset case scripts a USD-exhausted state (`eval/adk/scrum_team.evalset.json` has none),
  so no `EvalCase` needed updating.
