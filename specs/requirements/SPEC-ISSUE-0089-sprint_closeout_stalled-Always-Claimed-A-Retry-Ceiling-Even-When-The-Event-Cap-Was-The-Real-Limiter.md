# Issue

- Issue ID: SPEC-ISSUE-0089
- Title: `sprint_closeout_stalled` Always Claimed "This Harness's Own Retry Ceiling" Was Exhausted, Even When The Real Limiter Was The Per-Sprint Event Cap - And Its Only Self-Tuning Suggestion Can't See That Axis At All
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-11-12

## Overview
Reported while reviewing `0.2.0-run92` (dispatched against the `release-0.2.1` stabilization branch to
validate SPEC-ISSUE-0088's fix): the run stopped after 3 of 5 requested sprints with `report.md`'s
headline reading *"a sprint exhausted this harness's own retry ceiling before its close-out (sprint
report done, release PR) could finish"*. False in this specific run: `manifest.json`'s sprint 3 entry
shows `stop_reason: "max_events_reached"` (the flat 300-event-per-sprint cap), not
`max_nudges_exhausted` - the team's nudge-retry budget was barely touched. ScrumMaster's own sprint
report was genuinely produced (`sprint_report_pending_release: True` only because `create_release_pr`
itself never got called before the event cap hit), and its own `final_text` even claims "All sprint
close-out tasks are successfully finalized" - a confabulation the harness's own top-level label then
compounded by misattributing the real cause.

**Confirmed mechanism.** Both the run-level stderr print and the human-facing `report.md` explanation
for `sprint_closeout_stalled` hardcoded the same "retry ceiling" wording for every abort along this
path, regardless of which of several genuinely distinct per-sprint stop conditions
(`max_nudges_exhausted`/`max_events_reached`/`max_duration_exceeded`) actually ended the triggering
sprint:
- `agents/scrum_team/scripts/run_eval.py:1162-1169` (before this fix): the `else` branch of the
  abort-log print unconditionally said "exhausted this harness's own retry ceiling..." whenever
  `abort_reason != "budget_critical_halt"`.
- `agents/scrum_team/scripts/run_eval_analysis.py:519`: `_STOP_REASON_EXPLANATIONS["sprint_closeout_stalled"]`
  was a single static string with the same claim, with no way to vary by the real underlying cause.

This is a narrower sibling of the diagnostic-wording defect SPEC-ISSUE-0086 already fixed one level up
(that one fixed `budget_critical_halt` vs `sprint_closeout_stalled` at the run-level label; it never
touched this explanatory sub-text one level deeper, which keeps asserting *which specific mechanism*
caused the stall).

**Separately, the report's only self-tuning feedback can't see this failure mode at all.**
`_sprint_length_feedback` (`agents/scrum_team/tools/budget.py:486-538`) is the one place this harness
ever suggests a concrete config change ("Suggested new per-sprint token budget: ~X tokens") - but it
runs *inside* the agent session, computing only from `session.state["token_usage"]`; it has no access
to `event_count`/`max_events` at all (those are host-loop concepts in `run_eval.py`, never written
back into session state). `run92`'s sprint 3 used 5,524,015 of its 5,000,000 token budget (110%,
comfortably explained by the work actually done) while separately hitting the 300-event cap first -
so the report's one auto-generated recommendation ("raise `SPRINT_TOKEN_BUDGET`") would have done
nothing for the problem that actually happened, and nothing anywhere flagged that mismatch.

**Not in scope / explicitly not a bug:** the decision to abort the *entire run* after sprint 3, rather
than attempting a "sprint 4" continuation to finish the stalled release, is deliberate, documented
behavior (`_sprint_should_abort_run`/`_abort_run_stop_reason`, SPEC-ISSUE-0067's own Notes: "the
better fix... gives the team a real chance to finish the release within the same sprint rather than
burning a whole extra sprint's budget" - SPEC-ISSUE-0066's same-sprint nudging already tries that;
SPEC-ISSUE-0067 is the deliberate last-line-of-defense once that still isn't enough). Changing that
tradeoff is a product decision, not something this issue touches.

## Acceptance Criteria
- The abort-log stderr print (`run_eval.py`) and `report.md`'s stopped-early explanation
  (`run_eval_analysis.py`) must name the real underlying per-sprint `stop_reason` that triggered a
  `sprint_closeout_stalled` abort, not unconditionally assert "retry ceiling" - only the genuine
  `max_nudges_exhausted` case may still say that.
- When the real underlying cause was `max_events_reached`/`max_duration_exceeded`, `report.md` must
  explicitly note that raising the token budget will not help (since that axis wasn't the constraint),
  so a reader doesn't act on `_sprint_length_feedback`'s token-budget suggestion for a problem it can't
  fix.
- New regression tests replaying `0.2.0-run92`'s exact shape (abort triggered by a sprint whose own
  `stop_reason` is `max_events_reached`, not `max_nudges_exhausted`) and the original SPEC-ISSUE-0086
  shape (genuine `max_nudges_exhausted`) must both pass, each asserting the correct wording for its own
  case.

## Notes
- Gap lives in `agents/scrum_team/scripts/run_eval.py` (the abort-log print, now `_abort_log_text`) and
  `agents/scrum_team/scripts/run_eval_analysis.py` (`_STOP_REASON_EXPLANATIONS`/
  `_render_stopped_early_callout`).
- Confirmed via a live validation run (`0.2.0-run92`, `gh run view 43226595599`) dispatched specifically
  to verify SPEC-ISSUE-0088's fix - this diagnostic-wording gap was a secondary finding from reading
  that same run's manifest/report, not a separately reproduced eval run of its own.
- The missing event-cap-aware self-tuning suggestion (mirroring `_sprint_length_feedback`'s token-budget
  one) is deliberately NOT added as a numeric "suggested new event cap" recommendation - `max_events`
  itself (the cap value, e.g. 300) is never written into `manifest.json`/`sprint_result` at all, only
  the count reached (`event_count`), so there's no baseline to extrapolate a suggested new value from
  without a separate, larger harness change to persist the cap alongside the count. The qualitative
  "this wasn't a token problem" callout added here is the appropriately-scoped fix; a follow-up
  investigation could pursue surfacing the actual `max_events` value into the manifest if a future run
  shows this qualitative note still isn't enough to act on.

## Test Approach
- `agents/scrum_team/tests/test_run_eval.py`: new tests on `_abort_log_text` covering
  `max_events_reached` (must not say "retry ceiling", must name the real reason),
  `max_nudges_exhausted` (must still say "retry ceiling"), and `budget_critical_halt` (unaffected).
- `agents/scrum_team/tests/test_run_eval_analysis.py`: new tests on `_render_stopped_early_callout`
  covering the same `max_events_reached` (asserts "event cap" and "SPRINT_TOKEN_BUDGET" both appear,
  "retry ceiling" does not) and `max_nudges_exhausted` (asserts "retry ceiling" appears, "event cap"
  does not) cases, plus re-confirming the pre-existing SPEC-ISSUE-0086 regression test
  (`test_sprint_closeout_stalled_does_not_claim_a_budget_halt`, whose fixture uses bare placeholder
  sprint numbers rather than real sprint_result dicts) still passes unmodified.

## Resolution
`run_eval.py`'s abort-log text is now `_abort_log_text(sprint_result, abort_reason)`, a standalone pure
function (previously inlined in the sprint loop): budget-critical-halt unchanged; `max_nudges_exhausted`
still says "retry ceiling"; every other underlying `stop_reason` is named directly instead
(`f"hit its own {sprint_result['stop_reason']} with the sprint close-out..."`).

`run_eval_analysis.py` adds `_last_sprint_stop_reason`/`_sprint_closeout_stalled_explanation` -
`_render_stopped_early_callout` now keys the `sprint_closeout_stalled` explanation off
`manifest["sprints"][-1]["stop_reason"]` via `_SPRINT_CLOSEOUT_STALLED_UNDERLYING_REASONS`, falling back
to the original generic text if that entry isn't a real sprint_result dict (defensive, not just for
test fixtures - a genuinely missing/malformed last entry shouldn't crash report rendering). When the
underlying reason is `max_events_reached`/`max_duration_exceeded`, an explicit extra line notes the
token-budget suggestion elsewhere in the report won't help and suggests reducing per-sprint scope or
raising the harness's own event/duration cap instead.

Five new regression tests (3 in `test_run_eval.py`, 2 in `test_run_eval_analysis.py`) all pass. Full
suite (`python3 run_tests.py`, host + `agents/scrum_team/tests` via Docker Compose) passes: 800 host
tests + 1270 agent-suite tests (138 subtests), zero failures, including the pre-existing SPEC-ISSUE-0086
regression test unmodified.
