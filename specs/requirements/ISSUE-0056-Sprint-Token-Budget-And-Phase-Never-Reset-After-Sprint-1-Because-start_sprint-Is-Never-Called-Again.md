# Issue

- Issue ID: ISSUE-0056
- Title: `start_sprint` Is Never Called Again After Sprint 1, So The Token Budget Never Resets And `current_sprint_phase` Gets Permanently Stuck On "Review, Retro & Release"
- Status: Ready
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-14

## Overview
Reported (maintainer, watching `0.1.0-run64` and a related local run): the per-sprint token budget
did not appear to reset even though a new sprint had clearly started, and separately, the team's
reported sprint *phase* looked wrong - implementation-shaped work still happening while the system
was behaving as though it were in the closing/retro/release phase.

**Confirmed mechanism, both symptoms share one root cause.** `start_sprint` (`agents/scrum_team/tools/
scrum.py:895`) is the *only* place that resets the per-sprint token/event budget - its own docstring
is explicit: *"Mechanically resets the sprint token/event budget and its sibling guard flags
(`sprint_budget_reset_state_delta`, tools/budget.py) for every sprint after the first... GH #413
removed [the old reset_sprint_budget tool] entirely and folded the reset in here instead."* There is
no other reset path anywhere in the codebase (confirmed: `sprint_budget_reset_state_delta` has exactly
one caller, `start_sprint` itself).

`0.1.0-run64`'s `transcript.md` shows exactly **one** `start_sprint` call in the entire 3-sprint run
(line 1464, inside Sprint 1) - zero calls in either Sprint 2 or Sprint 3's event logs. `manifest.json`
confirms the consequence directly: `token_usage.total` is `5,385,342` at the end of Sprint 1,
`5,905,166` at the end of Sprint 2, `6,600,696` at the end of Sprint 3 - strictly climbing, never
dropping back toward zero the way a real per-sprint reset would produce. `check_cost_budget_callback`
(`agents/scrum_team/agent.py:938`) and `current_sprint_phase` (`agents/scrum_team/helpers.py:789`)
both read this same never-reset cumulative total and compare it against the fixed
`SPRINT_TOKEN_BUDGET` (5,000,000) ceiling - meaning from Sprint 2 onward, every single check sees a
total that's *already* past the ceiling before any of that sprint's own work has even started.

`current_sprint_phase`'s own logic (`helpers.py:807-813`) is the direct link to the phase symptom:

```python
budget_exhausted = token_limit > 0 and token_usage >= token_limit
if all_accepted or budget_exhausted:
    return "Review, Retro & Release"
return "Development"
```

Once `token_usage.total` is stuck above `token_limit` for the rest of the run (as it is here from
Sprint 2 onward), this branch returns `"Review, Retro & Release"` regardless of whether that sprint's
own stories are anywhere near accepted - the phase is pinned to "closing" essentially permanently,
even while `sprint_backlog_pr_missing(state) is None` (a real sprint backlog exists and is being
worked). Since `phase_awareness_injection_callback` (`agent.py:1653`) is explicitly "nudging only,
never a gate" (its own docstring), nothing mechanically stops DevTeam from continuing real
implementation work while the system is telling every role it's in the release/retro phase - exactly
the second symptom reported. `0.1.0-run64`'s own Sprint 2/3 happened to only involve leftover
acceptance/reporting/release calls (no fresh `write_file`/`start_feature_branch`), so this specific
run doesn't itself show a literal "DevTeam wrote code while in Review/Retro/Release" transcript line -
but the stuck-phase mechanism is identical and would produce exactly that whenever a later sprint's
own backlog still has real implementation work left.

**Why `start_sprint` never gets called again**: `run_eval.py`'s own scripted per-sprint message,
`_sprint_message` (`agents/scrum_team/scripts/run_eval.py:375-384`), never names `start_sprint` at
all - it explicitly names `create_sprint_report` and `create_release_pr` ("proceed directly through
planning, development, PR creation, CI verification, sprint review (`create_sprint_report`), and the
release PR (`create_release_pr`)...") but never tells the team the one tool call that actually starts
a new sprint mechanically. The only in-product nudge that *does* name it -
`SPRINT_PHASE_GUIDANCE["Plan & Start Sprint"]` ("Scrum Master calls `start_sprint(goal)` if that
hasn't happened yet this sprint") - is itself gated behind `current_sprint_phase` ever reaching that
branch, which (per the loop above) it cannot while the stale budget_exhausted check keeps returning
"Review, Retro & Release" instead. Once the first omission happens, the team has no remaining nudge
path back to the one tool that would fix it.

## Acceptance Criteria
- A new sprint's budget/phase state is not held hostage by whether the team happens to remember to
  call `start_sprint` again - either the scripted/real-world prompting reliably ensures it's called at
  every real sprint boundary, or the reset/phase-computation logic itself detects a new sprint has
  begun through some other, more robust signal (e.g. the harness's own sprint-boundary message) and
  self-heals, instead of silently inheriting the previous sprint's exhausted budget forever.
- `current_sprint_phase` must not become permanently stuck on "Review, Retro & Release" purely because
  a stale, never-reset cumulative budget counter happens to exceed the ceiling - a mechanical gap, not
  a reflection of this sprint's real backlog state.
- A new regression test: given a backlog with real `Development`-stage work left and a
  `token_usage.total` inherited from a prior sprint that already exceeds `SPRINT_TOKEN_BUDGET`,
  `current_sprint_phase` must not return "Review, Retro & Release" once a genuinely new sprint has
  started (via whatever the fix's real reset signal is) - only when *this* sprint's own usage
  legitimately exhausts its own fresh budget.
- `run_eval.py`'s scripted `_sprint_message` explicitly names `start_sprint` the same way it already
  names `create_sprint_report`/`create_release_pr`, at minimum as a stopgap while the more robust fix
  above is designed - cheap, falsifiable, and directly addresses why `0.1.0-run64` never called it
  again.

## Notes
- Gap lives in three places that all need to agree on when a sprint truly "starts": `start_sprint`
  (`agents/scrum_team/tools/scrum.py:895-941`, the only reset path), `current_sprint_phase`
  (`agents/scrum_team/helpers.py:789-818`, reads the now-stale counter with no other signal), and
  `run_eval.py`'s `_sprint_message` (`agents/scrum_team/scripts/run_eval.py:375-384`, never prompts for
  the one call that would keep all of this in sync).
- Distinct from ISSUE-0055: that issue is about ADK's own per-call context size growing unboundedly
  (inflating the *real* cost of every call). This issue is about the *budget accounting and phase
  state* never resetting at all - a story could in principle have a cheap, well-bounded context per
  call (ISSUE-0055 fixed) and still trip this bug, since `token_usage.total` would still never clear
  without a `start_sprint` call. Both are real, independent, and likely compound in practice: once
  `current_sprint_phase` wrongly reports budget exhaustion, the team is pushed into the
  `check_cost_budget_callback` grace-allowance path early, which is exactly the kind of
  rushed/short-circuited close-out `0.1.0-run64`'s Sprint 3 shows (critical halt, release PR opened but
  never merged).
- Not confirmed here, worth checking in a follow-up run: whether `sprint_goal`/the harness-internal
  notion of "which sprint number this is" (also only ever updated inside `start_sprint`) is similarly
  stuck at its Sprint-1 value for the rest of the run - `manifest.json`'s per-sprint `sprint_result`
  dict doesn't surface a raw `sprint_goal` field to check this directly from the artifact alone.

## Test Approach
- `agents/scrum_team/tests/test_agent.py` or `test_helpers.py` (wherever `current_sprint_phase` is
  tested): a case with `sprint_backlog_pr_missing` returning `None` (sprint backlog published), real
  `Development`-stage stories still incomplete, and `token_usage.total` already exceeding
  `budgets.total` (simulating the inherited-stale-budget condition) - assert the phase is not silently
  forced to "Review, Retro & Release" once the fix's real new-sprint signal says a fresh sprint has
  begun.
- `agents/scrum_team/tests/test_run_eval.py`: assert `_sprint_message`'s returned text names
  `start_sprint` explicitly (a cheap, literal string-presence check as the stopgap fix).
- Re-run (or replay) `0.1.0-run64`'s Sprint 2/3 shape against the fixed harness and confirm
  `token_usage.total` actually drops back near zero at each sprint boundary, and that `current_sprint_phase`
  tracks that sprint's own real backlog progress again.
