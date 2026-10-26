# Issue

- Issue ID: SPEC-ISSUE-0066
- Title: `_run_one_sprint`'s Own Stop Condition Declares A Sprint Done As Soon As A Report Appears - Never Checking The Release PR Also Went Out
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-26

## Overview
Reported (`0.1.0-run70`'s `report.md`, Top Problem #1): "Roadmap and story checklist states are
completely out of sync with actual codebase capabilities" - `specs/ROADMAP.md` shows US-0002 through
US-0006 stuck at READY for the whole run, Velocity flatlined at 0 after Sprint 1, and the run stopped
early (`blocked_needs_human` against US-0002) after burning its entire 5,024,360-token Sprint 5 budget
on a 6-hop ProductOwner/ScrumMaster transfer loop (`transcript.md:7007`).

**Confirmed mechanism.** Sprint 4 (`transcript.md:4558-6052`, 87 events, `manifest.json` sprint 4:
`stop_reason: "sprint_report_produced"`, `critical_halt: false`) never calls `create_release_pr` even
once - the only `create_release_pr` calls in the whole transcript are in Sprints 1, 2, 3, and 5
(`transcript.md:3447`, `3939`, `4494`, `9191`). Late in Sprint 4, DevTeam tries to plan new work for
US-0002 and is correctly refused:

```
transcript.md:6007-6008
"Cannot plan new sprint work - the previous sprint's retrospective/report was completed, and every
one of its planned stories reached Accepted, but create_release_pr was never called (or didn't
succeed) for it. A finished sprint is not actually closed until its release PR is opened..."
```

DevTeam transfers to ScrumMaster, who calls `record_human_approval` ("Approving release for Sprint 4 /
v0.1.0 increment.", `transcript.md:6029-6034`) - clearly heading toward `create_release_pr` next - but
Sprint 4's event log ends right there (event 87, no further tool call). `manifest.json` confirms the
resulting state: `sprint_report_pending_release: true` for sprint 4, carried straight into Sprint 5.

The actual trigger is `_run_one_sprint`'s own per-sprint turn loop
(`agents/scrum_team/scripts/run_eval.py:755-760`):

```python
session = await session_service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
current_report = session.state.get("sprint_report")
if current_report and current_report != report_baseline:
    sprint_report = current_report
    stop_reason = "sprint_report_produced"
    break
```

This fires as soon as a *new* `sprint_report` exists in state, with no check on
`sprint_report_pending_release` at all. Sprint 4's report had already been authored earlier in the
same invocation (before event 82's rejected `plan_sprint_backlog_item` call), so the very next time the
model's turn naturally finished (after `record_human_approval`, with no further tool call queued), this
check saw "a new report exists" and immediately broke the sprint's event loop - one turn before
ScrumMaster could reach `create_release_pr`.

`_sprint_should_abort_run` (same file, lines 420-478) already encodes the correct invariant for exactly
this shape of problem - its own docstring (GH #421) states: *"`stop_reason == sprint_report_produced`
alone is NOT sufficient proof the sprint ended cleanly... requires `sprint_report_pending_release` to
also already be `False`"* - and an existing test
(`agents/scrum_team/tests/test_run_eval.py::test_sprint_should_abort_when_the_report_was_produced_but_the_release_never_went_out`)
locks that in. But that check only ever runs as part of deciding whether to **abort the whole 5-sprint
run**, gated behind `critical_halt` being `True` (see SPEC-ISSUE-0067) - it has no bearing on whether an
**individual sprint's own loop** stops too early in the first place, which is a separate code path
(lines 755-760 above) that never consulted the same invariant. Sprint 4 here never hit a critical
budget halt (`critical_halt: false`) - it simply ran out of turns one step before the release PR - so
`_sprint_should_abort_run` wasn't even in a position to catch it, and the run proceeded into Sprint 5
believing Sprint 4 had closed cleanly.

With `sprint_report_pending_release` stuck `True`, `current_sprint_phase()`
(`agents/scrum_team/helpers.py:886-914`) returned `"Review, Retro & Release"` for the entirety of
Sprint 5. ScrumMaster's own `start_sprint` call was correctly refused by the SPEC-ISSUE-0057 phase gate
(`transcript.md:6085`) and ScrumMaster correctly pivoted straight to the close sequence instead of
retrying - but `create_sprint_report`'s own GH-#246 gate
(`agents/scrum_team/tools/budget.py:1271-1292`) then required a story to be freshly advanced to
`"Tested"` since QA's last PR review, and no story could satisfy that: US-0002 through US-0006 were all
still stuck at READY (DevTeam was never able to plan/implement any of them, per the very rejection
above), and the only "advanceable" story, US-0001, had already been Accepted since Sprint 1 -
re-advancing it is a documented no-op (`"US-0001 already marked Tested."`,
`transcript.md:6810-6825`) that does not increment `tested_count` past `qa_tested_baseline`. The result
was an unsatisfiable catch-22 for the whole of Sprint 5: 24 phase-gate rejections across QA,
ScrumMaster, and ProductOwner (`transcript.md:6085-9112`), eventually tripping the mechanical 6-hop
transfer-loop breaker (`transcript.md:7007`) and escalating to `blocked_needs_human` against US-0002
(mischaracterized as a product blocker - the real blocker was systemic), while the sprint still ran to
its full 300-event cap anyway.

## Acceptance Criteria
- `_run_one_sprint`'s per-sprint stop condition only declares `stop_reason = "sprint_report_produced"`
  (and treats the sprint as cleanly finished) when the new report exists **and**
  `session.state.get("sprint_report_pending_release")` is falsy - mirroring the exact invariant
  `_sprint_should_abort_run` already enforces, but applied where the premature stop actually happens.
- If a report exists but the release is still pending, `_run_one_sprint` keeps nudging (same as if no
  report had appeared yet) up to the existing `max_nudges`/`max_events`/`max_duration` limits, giving
  the team further turns to actually call `create_release_pr` in the same sprint - consistent with
  SPEC-ISSUE-0058's mid-sprint `create_release_pr` -> `_merge_open_prs` trigger already in this file.
- A new regression test in `agents/scrum_team/tests/test_run_eval.py`: a fake runner/session that
  produces a new `sprint_report` while `sprint_report_pending_release` stays `True` must NOT stop the
  per-sprint loop early (`stop_reason` must not become `"sprint_report_produced"` on that turn) - the
  loop must keep nudging until either the release flag clears or a hard cap (`max_events`/
  `max_duration`/`max_nudges`) is hit. A second case confirms the existing, already-correct behavior is
  unchanged: a new report with `sprint_report_pending_release` already `False` still stops immediately.

## Notes
- Distinct from SPEC-ISSUE-0056/0058 (both about `start_sprint` being stuck/never-retried once a
  *later* sprint has already begun) and SPEC-ISSUE-0057 (the phase-gate mechanism itself, which is
  working exactly as designed here - its identical-retry override correctly let QA's `check_build`/
  `advance_story_stage`/`merge_story_pr` calls through once retried; see `transcript.md:6726-6744`,
  `6798-6825`, `6844-6865`). This issue is about *where the stale `sprint_report_pending_release` flag
  itself first gets stuck* - one turn earlier than any previously-filed issue in this family, inside
  the harness's own per-sprint completion check rather than anything team/agent-facing.
- The `create_sprint_report` gate added for GH issue #246 (`budget.py:1251-1292`, requiring a fresh
  `Tested` transition before closing) is working as intended in isolation - it correctly stops QA from
  skipping real verification. It only became unsatisfiable here as a downstream consequence of Sprint 4
  never getting to implement new work at all; no separate fix to that gate is needed once this issue's
  fix prevents Sprint 4 from ending prematurely in the first place.

## Test Approach
- `agents/scrum_team/tests/test_run_eval.py`: extend the existing `_run_one_sprint` stop-condition
  coverage (see `test_run_one_sprint_reports_whether_the_release_is_still_pending`) with a case where
  the fake session produces a new report but leaves `sprint_report_pending_release=True` across
  multiple nudges - assert the loop only stops once either the flag clears or `max_events`/
  `max_nudges`/`max_duration` is actually hit, and `stop_reason` reflects whichever happened.
- Full `agents/scrum_team/tests` suite re-run after the change.

## Resolution
- `agents/scrum_team/scripts/run_eval.py` (`_run_one_sprint`): the per-sprint turn loop now only
  declares `stop_reason = "sprint_report_produced"` (and breaks) once a new report exists **and**
  `session.state.get("sprint_report_pending_release")` is falsy. The new report is still captured into
  `sprint_report` as soon as it appears either way - so downstream metrics/manifest reporting and
  `_sprint_should_abort_run` (SPEC-ISSUE-0067) both see it - but the loop keeps nudging instead of
  breaking while the release is still pending, giving the team real further turns (up to the existing
  `max_nudges`/`max_events`/`max_duration` caps) to actually call `create_release_pr` in the same
  sprint.
- `agents/scrum_team/tests/test_run_eval.py`: new
  `test_run_one_sprint_keeps_nudging_when_a_report_appears_but_the_release_is_still_pending` (the fix)
  and `test_run_one_sprint_still_stops_immediately_once_the_release_has_also_gone_out` (confirms the
  already-correct clean-close-out case is unchanged).
- Full `agents/scrum_team/tests` suite re-run (1082 passed, 133 subtests passed) via this repo's
  Docker-based `run_tests.py` harness (required - `agent.py`'s module-level `_setup_logging()` hardcodes
  `/app/sessions`, so the suite can only run inside the container, not directly against a host
  checkout).
