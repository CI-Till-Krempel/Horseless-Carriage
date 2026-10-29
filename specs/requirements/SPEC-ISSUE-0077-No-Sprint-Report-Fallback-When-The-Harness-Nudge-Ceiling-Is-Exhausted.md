# Issue

- Issue ID: SPEC-ISSUE-0077
- Title: `_run_one_sprint`'s Sprint-Report Fallback Only Ever Fires On A Budget-Critical Halt - A Sprint That Exhausts The Harness's Own Nudge Ceiling Gets No Report, No Transcript, Nothing
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-29

## Overview
Reported by the maintainer, reviewing `0.1.0-run76` manually: Sprint 4 has no sprint report and no
transcript at all in the eval repo - the file sequence jumps straight from `SPRINT-REPORT-003.md`/
`TRANSCRIPT-003.md` to `SPRINT-REPORT-005.md`/`TRANSCRIPT-005.md`
(confirmed via `gh pr view 639 --repo CI-Till-Krempel/horseless-carriage-eval-todo-app`, the run's
overview PR - `specs/reports/SPRINT-REPORT-004.md`/`TRANSCRIPT-004.md` simply aren't in the diff).

**Confirmed mechanism.** Sprint 4 stopped with `manifest.json`'s `stop_reason: "max_nudges_exhausted"`
after only 636,152 of its 5,000,000 token budget (12.7%) - nowhere near budget-constrained.
`transcript.md`'s own raw log (sprint 4 section) shows exactly why: DevTeam tried to plan
`SPEC-ISSUE-0003` into the sprint, hit `plan_sprint_backlog_item`'s phase-gate rejection
("does not belong in the current phase ('Development')..."), retried as the gate's own documented
"explicit override," then hit the ordering gate ("the higher-priority story 'SPEC-ISSUE-0002' must
reach Accepted first"), switched to `SPEC-ISSUE-0002`, read its spec, and was mid-retry on the *same*
phase-gate rejection a second time when the transcript's `final_text` cuts off - the sprint's fixed
nudge ceiling (`run_eval.py`'s `max_nudges`, default 4 "continue" retries) ran out before Scrum Master
ever got a turn to run `create_sprint_report`.

`_run_one_sprint`'s own sprint-report safety net (`agents/scrum_team/scripts/run_eval.py`) only ever
fired on `session.state.get("critical_halt_notified")` - set exclusively by
`check_cost_budget_callback`'s real budget-exhaustion path (`agent.py`'s `_notify_critical_halt`).
Since Sprint 4 never hit a budget wall, that flag was never set, the safety net never ran, and
`sprint_report`/`transcript` both stayed `None` all the way to the function's return - confirmed by
`manifest.json`'s sprint 4 entry: `sprint_report: null`. This is a second, independent dead end from the
one SPEC-ISSUE-0045 already fixed (a non-grace role frozen by a real budget halt, unable to transfer) -
that fix, and this safety net's own trigger condition, both assumed "sprint ended with no report" always
means "budget ran out," which is not true for the harness's own, separate nudge-ceiling mechanism.

A second, compounding gap: even when the safety net *does* fire (the real budget-halt case),
`render_fallback_sprint_report` (`agents/scrum_team/tools/budget.py`) never called
`_write_conversation_transcript` - only `_write_retro_and_steering_docs`. `create_sprint_report` itself
always renders both; the fallback path only ever rendered the report, meaning *any* fallback-triggered
sprint close (budget or nudges) was already losing its transcript file, not just the report - the
report-only half of this gap happened to be invisible until a sprint hit the never-covered nudges case.

## Acceptance Criteria
- A sprint that genuinely started (`sprint_number` set) and ends with no `sprint_report`, for *any*
  `stop_reason`, must get a mechanically-rendered fallback report and transcript committed - not only
  when `critical_halt_notified` was set.
- The fallback report's own explanatory text must not claim "budget used in full" when that's false -
  it must distinguish at least the real budget-exhaustion case from the harness's own nudge-ceiling
  case, with a third, generic fallback for any other/future stop reason.
- `render_fallback_sprint_report` must render a transcript file (`_write_conversation_transcript`)
  alongside the report, matching `create_sprint_report`'s own behavior - regardless of which reason
  triggered the fallback.
- Must not regress the existing, working budget-critical-halt path (SPEC-ISSUE-0045) - its wording,
  commit message, and release-PR title must stay byte-identical to before this fix for every existing
  caller that doesn't pass a reason explicitly.
- A new regression test replaying this run's exact shape: a sprint with `sprint_number` set, no
  `critical_halt_notified`, `stop_reason: "max_nudges_exhausted"`, and no `sprint_report` produced -
  assert the fallback fires, a report and transcript both get written, and the report's own wording
  does not claim the budget was used in full.

## Notes
- Distinct from SPEC-ISSUE-0045's "a non-grace role freezes mid-budget-halt" root cause - that fix
  (grace-role hand-off) correctly prevents ITS specific dead end, but this harness-level backstop's own
  trigger condition was never broadened to cover every other way a sprint can end with no report. The
  two are siblings, not duplicates.
- The nudge-exhaustion dance itself (two retro-filed Issues each needing a phase-gate-override retry,
  one chained after the other via the ordering gate) is a secondary efficiency concern, not something
  this issue tries to fix - SPEC-ISSUE-0076 (positioning a retro Issue correctly) and the existing
  "immediate retry override" pattern are the relevant mechanisms there. This issue is purely about the
  harness guaranteeing a report/transcript always exists regardless of how long that dance takes.

## Test Approach
- `agents/scrum_team/tests/test_run_eval.py` (`TestHostSideSprintReportBackstop`):
  `test_calls_the_backstop_when_the_nudge_ceiling_is_exhausted_even_without_a_critical_halt` - the
  direct regression test (`sprint_number` set, no critical halt, `_NoProgressRunner`/`max_nudges=0`
  yielding `stop_reason == "max_nudges_exhausted"`) - and
  `test_backstop_passes_the_budget_reason_when_a_critical_halt_was_notified` confirming the existing
  path's `reason="budget"` is passed explicitly, not just defaulted into.
- `agents/scrum_team/tests/test_budget.py` (`TestRenderFallbackSprintReport`):
  `test_writes_a_transcript_alongside_the_report`, `test_reason_nudges_does_not_claim_the_budget_was_used_in_full`,
  `test_reason_budget_keeps_the_original_wording`, `test_unrecognized_reason_renders_a_generic_reason_agnostic_paragraph`,
  `test_default_reason_is_budget_for_backward_compatibility`.
- `agents/scrum_team/tests/test_agent.py` (`TestEnsureSprintReportOnFinalHalt`):
  `test_passes_the_nudges_reason_through_to_the_report_and_commit_wording` - confirms the commit
  message/release-PR title/body don't claim "budget exhausted" for the nudges case.

## Resolution
Broadened `_run_one_sprint`'s safety-net trigger (`agents/scrum_team/scripts/run_eval.py`) from
`session.state.get("critical_halt_notified")` to `not sprint_report and session.state.get("sprint_number",
0) > 0` - fires for any genuinely-started sprint that ends without a report, for any `stop_reason`.
Derives a `reason` ("budget" only when `critical_halt_notified` is actually set, "nudges" when
`stop_reason == "max_nudges_exhausted"`, "other" otherwise) and passes it through
`_ensure_sprint_report_on_final_halt` (`agents/scrum_team/agent.py`, now accepting a `reason: str =
"budget"` parameter, defaulting to the original behavior for every existing in-session caller) to
`render_fallback_sprint_report` (`agents/scrum_team/tools/budget.py`, same new parameter/default).

`render_fallback_sprint_report` now renders a reason-specific explanatory paragraph ("Budget Used in
Full" for "budget", "Harness Retry Ceiling Reached" - explicitly stating the budget was NOT exhausted -
for "nudges", a generic "Sprint Ended Without A Report" for anything else/unrecognized) instead of
unconditionally claiming budget exhaustion, and now also calls `_write_conversation_transcript` so a
transcript file is always committed alongside the report, matching `create_sprint_report`'s own
behavior - previously missing from this path entirely, for every reason, not just the new "nudges" case.
`_ensure_sprint_report_on_final_halt`'s own commit message and release-PR title/body are likewise
reason-aware (a small label map: "sprint budget exhausted" / "harness retry ceiling reached" / "sprint
ended without a report") rather than hardcoding budget-exhaustion wording unconditionally.

Tests added/updated:
- `test_run_eval.py`: added the two tests named in Test Approach; updated the three existing
  `TestHostSideSprintReportBackstop` tests that relied on `critical_halt_notified` alone to also set
  `sprint_number` (now required for the backstop to fire) and accept the new `reason` kwarg their
  `side_effect` fakes receive; renamed `test_does_not_call_the_backstop_without_a_critical_halt` to
  `test_does_not_call_the_backstop_when_no_sprint_has_actually_started` to reflect the real remaining
  no-op case (no `sprint_number` at all) rather than the no-longer-accurate "no critical halt" framing.
- `test_budget.py`: added the five `TestRenderFallbackSprintReport` tests named above.
- `test_agent.py`: updated `test_integrates_and_pushes_after_rendering_the_report` to assert
  `render_fallback_sprint_report` is called with the explicit `reason="budget"` default, and added
  `test_passes_the_nudges_reason_through_to_the_report_and_commit_wording`.

Full suite (`agents/scrum_team/tests`, via `docker compose run`, project-scoped per
`lib_docker.compose_project_args("test")`) passes 1120/1125 - the same 5 pre-existing failures already
tracked (fixed by SPEC-ISSUE-0074's companion conftest.py change, landing in a separate PR) present and
unrelated to this change.
