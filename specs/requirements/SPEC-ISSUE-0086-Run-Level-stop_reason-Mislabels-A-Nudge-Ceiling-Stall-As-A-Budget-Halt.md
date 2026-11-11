# Issue

- Issue ID: SPEC-ISSUE-0086
- Title: `run_eval.py`'s Run-Level `stop_reason` Hardcodes `"budget_critical_halt"` Even When No Budget Halt Was Ever Notified
- Status: Ready
- Priority: Should
- Owner: Architect
- Last Updated: 2026-11-11

## Overview
`0.2.0-run88` (`gh run 43010932390`, artifact `eval-report-0.2.0-run88`) stopped after 2 of 5
requested sprints. `report.md`'s headline reads:

> ⚠️ Evaluation Stopped Early - Only 2 of 5 requested sprints completed - **a sprint hit a critical
> token/USD budget halt with no clean close-out afterward** - Stop reason: `budget_critical_halt`

This is false. `manifest.json`'s own sprint 2 entry records `"critical_halt": false` and
`"stop_reason": "max_nudges_exhausted"` - no budget halt was ever notified during this run; Sprint 2
simply ran out of this harness's fixed per-sprint "continue" retry ceiling (`max_nudges`) before
Scrum Master's close-out sequence finished, leaving `"sprint_report_pending_release": true`.

**Confirmed mechanism.** `_sprint_should_abort_run` (`agents/scrum_team/scripts/run_eval.py:438-504`)
already distinguishes the two cases internally:

```python
# run_eval.py:501-504
cleanly_closed = bool(sprint_result.get("sprint_report")) and not sprint_result.get("sprint_report_pending_release")
if sprint_result["critical_halt"]:
    return not cleanly_closed
return bool(sprint_result.get("sprint_report")) and bool(sprint_result.get("sprint_report_pending_release"))
```

For sprint 2, `critical_halt` was `False`, so the function took the *second* branch - a sprint that
produced a report (via `_ensure_sprint_report_on_final_halt`'s nudges-ceiling fallback, see
SPEC-ISSUE-0077) but never got its release PR out. The caller, though, doesn't look at *which* branch
fired - it unconditionally hardcodes the budget label:

```python
# run_eval.py:1140-1148
if _sprint_should_abort_run(sprint_result):
    print(
        f"--- sprint {sprint_number}/{args.sprints} hit a critical budget halt with no clean "
        "sprint close-out - stopping run ---",
        file=sys.stderr,
    )
    manifest["stopped_early"] = True
    manifest["stop_reason"] = "budget_critical_halt"
    break
```

`run_eval_analysis.py:518` then maps the literal string `"budget_critical_halt"` straight to the
report's "a sprint hit a critical token/USD budget halt..." sentence, with no awareness that this
particular `stop_reason` can also be reached via the nudges-pending-release branch.

This is the same diagnostic-wording defect SPEC-ISSUE-0077 already fixed once - but in a different,
sibling call site. SPEC-ISSUE-0077 correctly broadened `_run_one_sprint`'s own *in-sprint* fallback
(`_ensure_sprint_report_on_final_halt`'s `reason` parameter, `run_eval.py:864-883`) to say "budget"
only `if session.state.get("critical_halt_notified")`, "nudges" for `max_nudges_exhausted`, "other"
otherwise - and that distinction is available right there in `sprint_result["critical_halt"]`. The
*run-level* `manifest["stop_reason"]` assignment at line 1147, one level up in the same file, was
never updated to use it, so the exact same category of misleading "it was a budget problem" claim
SPEC-ISSUE-0077 eliminated from the sprint report text is still reachable through the run's own
top-level manifest/report headline.

This is purely a diagnostic-wording gap, not a correctness bug in the stopping decision itself:
`_sprint_should_abort_run` is right to stop the run either way (a stuck `sprint_report_pending_release`
is unsafe to continue from regardless of cause, per its own docstring) - the only thing wrong is the
label a human reads afterward, which sends anyone debugging a nudges-ceiling stall straight at the
wrong mechanism (token/cost budgets) instead of the real one (the harness's own fixed retry ceiling
colliding with a close-out sequence that needed one more turn).

## Acceptance Criteria
- The run-level `manifest["stop_reason"]` set when `_sprint_should_abort_run` returns `True` must
  distinguish a genuine critical-budget-halt abort (`sprint_result["critical_halt"]` was `True`) from
  a nudges-ceiling-with-pending-release abort (`critical_halt` was `False`) - two different stop
  reason strings (or an equivalent structured field), not one hardcoded `"budget_critical_halt"` for
  both.
- `run_eval_analysis.py`'s `_STOP_REASON_EXPLANATIONS` mapping (and the report headline it produces)
  must not describe a nudges-ceiling stall as a "token/USD budget halt."
- A new regression test replays this run's exact shape (`critical_halt=False`,
  `stop_reason="max_nudges_exhausted"`, a non-empty `sprint_report`, `sprint_report_pending_release=True`)
  and asserts the manifest's run-level stop reason does not read as a budget halt.
- Must not regress the existing, correct budget-halt case (`critical_halt=True`, unclean close-out) -
  that path's existing wording/behavior stays as-is.

## Notes
- Lives in `agents/scrum_team/scripts/run_eval.py:1140-1148` (the `_sprint_should_abort_run` call
  site) and `agents/scrum_team/scripts/run_eval_analysis.py:518` (`_STOP_REASON_EXPLANATIONS`) - no
  other file needs to change. `_sprint_should_abort_run` itself (`run_eval.py:438-504`) does not need
  to change; it already has the information needed (`sprint_result["critical_halt"]`), it's just not
  threaded through to the caller's label.
- Distinct from SPEC-ISSUE-0077, which fixed the equivalent mislabeling one level down, inside the
  per-sprint fallback report/commit-message text itself - this issue is about the outer run loop's own
  `manifest["stop_reason"]`/report headline, a separate call site SPEC-ISSUE-0077 didn't touch.
- `0.2.0-run88` also surfaced a secondary, likely-related effect worth a retro note rather than a new
  Issue: Sprint 1's single DevTeam development pass wrote the full CRUD implementation for all 6
  planned user stories (US-0001 through US-0006 - list/task create, toggle, delete, in
  `app.py`/`templates/index.html`, `transcript.md:1710-1737`) under cover of the one PR it had
  actually tracked (`US-0001`'s feature branch), then only ever called `advance_story_stage` through
  `"Implemented"`/`"Accepted"` for `US-0001` itself. US-0002 through US-0006 are fully working in the
  shipped code but permanently stuck at `stages_completed: ["Draft", "Ready"]`
  (`manifest.json`, sprint 2's `product_backlog` snapshot) - `report.md`'s own Top Problem #1. No
  code-level guardrail currently has any way to detect "this diff also happens to satisfy another
  backlog item's acceptance criteria" short of real semantic analysis, so this doesn't have an obvious
  falsifiable fix shape yet; flagging it here for whoever investigates the next run where it recurs.

## Test Approach
- `agents/scrum_team/tests/test_run_eval.py`: new test constructing a `sprint_result` dict with
  `critical_halt=False`, `stop_reason="max_nudges_exhausted"`, a truthy `sprint_report`, and
  `sprint_report_pending_release=True`, driving (or directly unit-testing the extracted labeling logic
  around) the call site at `run_eval.py:1140-1148`, asserting the resulting `stop_reason` is not
  `"budget_critical_halt"`. A second test keeps the existing `critical_halt=True` case asserting the
  original label/behavior is unchanged.
- `agents/scrum_team/tests/test_run_eval_analysis.py` (or equivalent): assert the new nudges-ceiling
  stop reason string maps to explanatory text that does not mention "token/USD budget."
