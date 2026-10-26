# Issue

- Issue ID: SPEC-ISSUE-0067
- Title: `_sprint_should_abort_run` Only Checks For A Stuck `sprint_report_pending_release` On The `critical_halt` Path - A Non-Critical-Halt Sprint Ending Mid-Close-Out Is Waved Through As Clean
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-10-26

## Overview
Found while investigating `0.1.0-run70` (see SPEC-ISSUE-0066 for the full mechanism of how Sprint 4
there ended with `sprint_report_pending_release` stuck `True` via a non-critical-halt path). This issue
is the second, independent gap the same investigation surfaced: even after SPEC-ISSUE-0066's fix closes
off the specific way that happened in run #70, nothing stops the *general* case - a sprint whose own
turn loop exhausts `max_nudges`/`max_events`/`max_duration` while the release step is still incomplete,
without ever triggering a critical budget halt - from being treated as safe to continue from.

`_sprint_should_abort_run` (`agents/scrum_team/scripts/run_eval.py:420-478`) is the harness's only
check for "should the whole 5-sprint run stop here instead of continuing to the next sprint's fresh
state". Its body:

```python
return bool(sprint_result["critical_halt"]) and not (
    bool(sprint_result.get("sprint_report"))
    and not sprint_result.get("sprint_report_pending_release")
)
```

Note the leading `bool(sprint_result["critical_halt"]) and ...` - if `critical_halt` is `False`, the
whole expression short-circuits to `False` ("don't abort") **regardless of what
`sprint_report_pending_release` or `sprint_report` say**. This is also directly locked in by an existing
test, whose own docstring already names the gap as a known, accepted limitation rather than something
ruled out elsewhere:

```python
# agents/scrum_team/tests/test_run_eval.py:337-343
def test_sprint_should_not_abort_without_a_critical_halt_even_if_release_is_still_pending():
    """A non-critical-halt sprint ending mid-close-out is an entirely
    different (and already-handled) signal - this function only ever fires
    on critical_halt in the first place."""
    assert _sprint_should_abort_run({
        "critical_halt": False, "stop_reason": "sprint_report_produced", "sprint_report_pending_release": True,
    }) is False
```

`0.1.0-run70`'s Sprint 4 is the counterexample to that docstring's own assumption: it ended with
exactly this combination (`critical_halt: false`, `sprint_report_pending_release: true`) via its own
per-sprint turn loop simply running out of turns one step before `create_release_pr` - not via any
budget-halt path - and nothing else in the harness caught it before Sprint 5 began. "Already-handled" by
SPEC-ISSUE-0066's fix narrows how *often* this combination can arise (a `sprint_report_produced` stop
specifically can no longer carry a stuck pending-release flag), but a sprint can still exhaust
`max_nudges`/`max_events`/`max_duration` while genuinely still mid-close-out and non-critically-halted -
e.g. the team keeps correctly retrying `create_release_pr` per SPEC-ISSUE-0058's mid-sprint recovery
path but never succeeds before time/events run out. That case is untouched by this function today.

## Acceptance Criteria
- `_sprint_should_abort_run` evaluates the "report exists but release never went out" condition
  independently of `critical_halt` - a sprint ending with `sprint_report_pending_release: true` for ANY
  reason (critical halt or not) should be treated as not cleanly closed, and the run should abort rather
  than start the next sprint's fresh session against a team structurally blocked by the inherited phase.
- A sprint with no `sprint_report` at all (genuinely nothing produced yet, e.g. still early in a
  healthy sprint that simply hit `max_events_reached` mid-development) must NOT abort on this basis
  alone - this function's existing `critical_halt`-gated abort-on-no-report behavior for that case stays
  intact; only the "report exists, release still pending" condition becomes unconditional.
- Existing test `test_sprint_should_not_abort_without_a_critical_halt_even_if_release_is_still_pending`
  is updated to assert the corrected behavior (`True`, not `False`) with its docstring rewritten to
  describe the fix rather than the old assumption; all other existing `_sprint_should_abort_run` tests
  continue to pass unchanged, since none of them have `sprint_report_pending_release: true` with
  `critical_halt: false` as an expected-clean case.

## Notes
- This is a defense-in-depth fix layered on top of SPEC-ISSUE-0066, not a substitute for it -
  SPEC-ISSUE-0066 prevents the premature per-sprint stop from happening in the first place (the better
  fix, since it gives the team a real chance to finish the release within the same sprint rather than
  burning a whole extra sprint's budget before the run notices); this issue makes sure the *harness's
  last line of defense* doesn't silently wave through the same stuck state if a sprint somehow still
  ends with it set, from any cause.
- Same file/function family as SPEC-ISSUE-0058's `_merge_open_prs`/mid-sprint recovery work; whoever
  implements this should re-run that issue's regression tests
  (`TestRunOneSprintMidSprintReleasePrMerge`, `TestUnresolvableReleasePrConflict`) alongside the new one
  here, since all three touch the same sprint-closeout decision surface.

## Test Approach
- `agents/scrum_team/tests/test_run_eval.py`: update
  `test_sprint_should_not_abort_without_a_critical_halt_even_if_release_is_still_pending` to assert
  `True`; add a companion case confirming a non-critical-halt sprint with no report at all still does
  NOT abort on this basis (only the pending-release condition is now unconditional, not the whole
  function).
- Full `agents/scrum_team/tests` suite re-run after the change.

## Resolution
- `agents/scrum_team/scripts/run_eval.py` (`_sprint_should_abort_run`): restructured so the
  "report exists but release never went out" check (`sprint_report` truthy AND
  `sprint_report_pending_release` truthy) now applies unconditionally, not only when `critical_halt` is
  `True`. The existing `critical_halt`-gated "no clean close-out at all" behavior is preserved exactly
  as before on that path; only the non-critical-halt path gained the new, narrower check (a non-critical
  sprint with no report yet still correctly does not abort).
- `agents/scrum_team/tests/test_run_eval.py`: renamed and rewrote
  `test_sprint_should_not_abort_without_a_critical_halt_even_if_release_is_still_pending` to
  `test_sprint_should_abort_when_release_is_still_pending_even_without_a_critical_halt` (now asserts
  `True`), with its docstring explaining the old assumption was wrong; added
  `test_sprint_should_not_abort_without_a_critical_halt_when_nothing_was_ever_produced` to confirm the
  narrower, no-report case is unaffected.
- Full `agents/scrum_team/tests` suite re-run (1082 passed, 133 subtests passed), same Docker-based
  harness as SPEC-ISSUE-0066.
