# Issue

- Issue ID: ISSUE-0049
- Title: Eval Harness's Per-Sprint Reset Drifted From reset_sprint_budget, Losing Sprint 3's Report Entirely
- Status: Done
- Priority: Must
- Owner: ScrumMaster
- Last Updated: 2026-09-24

## Overview
Reported (maintainer, reviewing `0.1.0-run33`): a 5-sprint evaluation run stopped after only 3 sprints,
and Sprint 3 - unlike Sprint 2, which hit the same token-budget wall but still produced a fallback report -
produced **no sprint report at all** (`EVAL-REPORT.md`'s Team Efficiency finding #1: "Sprint 2 and Sprint 3
suffered catastrophic budget exhaustion and pipeline lockup ... Sprint 3 shows 0 stories completed and no
report produced").

Investigated directly against the real run: `gh run view 35974931711` (`0.1.0-run33`), its
`report.md`/`manifest.json`/`transcript.md` artifacts, and the eval repo's actual branches/PRs
(`horseless-carriage-eval-todo-app`, `eval/0.1.0-run33/*`).

**Root cause:** `run_eval.py`'s `_run_one_sprint` resets a hand-copied subset of session state at the start
of every sprint (`state_delta` on `attempt == 0`) as a harness-side stand-in for the `reset_sprint_budget`
tool Scrum Master calls in interactive/real usage (there is no Scrum Master turn to call it for an
unattended eval run). That hand-copied dict only ever covered `token_usage`/`budget_exhaustion_synced` -
it predates two guard flags `reset_sprint_budget` (agent-invoked, `agents/scrum_team/tools/budget.py`) had
since grown to also clear: `critical_halt_notified` and `sprint_report_safety_net_fired` (the latter guards
`_ensure_sprint_report_on_final_halt_once`, `agent.py` - the exact safety net that DID produce Sprint 2's
fallback report). The two copies were never kept in sync.

Sprint 2 ended via a critical halt whose fallback-report safety net fired, setting
`sprint_report_safety_net_fired = True` in session state. Sprint 3 began, immediately re-hit the same token
ceiling (confirmed in `transcript.md`: only 26 real events before the halt, `token_usage.total` reported as
6,038,895 - already over even `SPRINT_CLOSEOUT_GRACE_ROLES`' 20% grace ceiling of 6,000,000 for a
5,000,000 budget), and ProductOwner's own halt hit
`_ensure_sprint_report_on_final_halt_once` - which silently no-opped because the guard flag was still
`True` from Sprint 2's halt, never reset for the new sprint. Sprint 3 therefore ended with
`sprint_report: ""` and `stop_reason: max_nudges_exhausted`, and `_sprint_should_abort_run` correctly (by
its own, separate logic) then stopped the whole run rather than continue sprints 4-5 from an unclean state.

## Acceptance Criteria
- The eval harness's per-sprint reset and the real `reset_sprint_budget` tool can never independently
  drift apart again - one shared definition, not two hand-maintained copies.
- A sprint that ends via the final-halt safety net no longer leaves that safety net disarmed for the very
  next sprint: a fresh sprint's own final halt is guaranteed a report (real or fallback) again, regardless
  of whether the previous sprint's halt already fired it once.
- A regression test exercises `_run_one_sprint`'s actual `state_delta` payload against the shared reset
  definition, so a future hand-edit to either copy that reintroduces a mismatch fails a test instead of
  only surfacing in a real (paid) eval run.

## Notes
- Deliberately does not change `_sprint_should_abort_run`'s own logic (ISSUE-0045) - that behaved exactly
  as designed once Sprint 3's close-out failed uncleanly. The bug is upstream of it: Sprint 3's close-out
  failed uncleanly only because its own safety net was pre-disarmed by state left over from Sprint 2.
- Does not, by itself, explain why Sprint 3's `token_usage.total` was already this close to the grace
  ceiling after only 26 real events - see ISSUE-0050 (proposed, not yet fixed) for the separate,
  cross-sprint state-persistence gap implicated in that and in Sprint 2's own budget blowout.
- `budget_reset_since_last_sprint_start` (used by `start_sprint`'s own gate in real/interactive usage,
  GH issue #110) was also missing from the harness's old hand-copied dict. The eval harness never calls
  `start_sprint`, so this had no observable effect on `0.1.0-run33` specifically, but is included in the
  shared reset now for the same "one definition, not two" reason - a future harness change that does route
  through `start_sprint` should not have to rediscover this gap.

## Test Approach
- `agents/scrum_team/tests/test_budget.py::TestBudgetTools::test_reset_sprint_budget_clears_every_grace_and_safety_net_guard` -
  `reset_sprint_budget` clears `token_usage`, `budget_exhaustion_synced`, `budget_reset_since_last_sprint_start`,
  `critical_halt_notified`, and `sprint_report_safety_net_fired` even when all were left in a stale,
  post-halt state.
- `agents/scrum_team/tests/test_budget.py::TestBudgetTools::test_sprint_budget_reset_state_delta_returns_independent_copies` -
  two calls to the shared helper never alias the same nested dict.
- `agents/scrum_team/tests/test_run_eval.py::test_run_one_sprint_reset_state_delta_matches_shared_sprint_budget_reset` -
  the actual `state_delta` `_run_one_sprint` passes to `runner.run_async` on a sprint's first attempt is a
  superset of the shared reset dict (plus the harness's own extra `sprint_report`/`sprint_report_kpis` keys).
- Full `agents/scrum_team/tests` suite (via `docker compose -p horseless-carriage-test --env-file .env.test
  run --rm --entrypoint "" -e PYTHONPATH=/app agent pytest --cov=agents agents/scrum_team/tests`, with
  `db`/`litellm` up first): 635 passed, no regressions.

## Resolution
- `agents/scrum_team/tools/budget.py`: new `sprint_budget_reset_state_delta()` - the single source of truth
  for which state keys a new sprint clears, and to what. `reset_sprint_budget` now iterates this instead of
  hand-listing the same five assignments inline.
- `agents/scrum_team/scripts/run_eval.py`: `_run_one_sprint`'s `attempt == 0` `state_delta` now spreads
  `sprint_budget_reset_state_delta()` and layers its own extra `sprint_report`/`sprint_report_kpis` keys on
  top, instead of hand-listing a separate, smaller subset.
