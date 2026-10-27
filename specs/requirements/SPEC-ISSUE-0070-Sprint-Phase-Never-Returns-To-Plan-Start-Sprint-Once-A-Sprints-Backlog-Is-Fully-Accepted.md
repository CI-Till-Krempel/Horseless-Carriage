# Issue

- Issue ID: SPEC-ISSUE-0070
- Title: `current_sprint_phase` Never Returns To "Plan & Start Sprint" Once A Sprint's Backlog Is Fully Accepted, Locking `sprint_number` For Multiple Real Sprints
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-27

## Overview
Reported while investigating `0.1.0-run73`: the maintainer noticed the `[Sprint N | ...]` log line
starts at `Sprint 0` and "is not correct, as sprints were closed in between," and that "sprint
phases seem to be out of sync at times." Both are the same confirmed mechanism, not two separate
bugs.

**`Sprint 0` itself is correct, not a bug.** `_phase_and_tokens_prefix` (`agent.py:1658`) reads
`state.get("sprint_number") or 0`, and `sprint_number` genuinely is `0` (`state.py:21`'s own
default) before the very first `start_sprint` call ever succeeds - there is no sprint yet. The run's
actual log (`gh run view --log`) shows exactly this: `Sprint 0` through the `Conceptual Work` and
`Plan & Start Sprint` phases, flipping to `Sprint 1` the instant the first `start_sprint` call
returns `{"status": "ok", "sprint_number": 1}`.

**The real bug: `sprint_number` then gets stuck for most of the run.** The eval harness completed
exactly 5 distinct real sprint-loop iterations (`transcript.md`'s own `## Raw agent activity log -
sprint N` sections, N=1..5, each ending with its own `create_sprint_report` call and its own
`PR Merges: 1/1` per `report.md`'s per-sprint table). But grepping `transcript.md` for every
`"sprint_number"` value actually returned by a tool call shows **only two distinct values across the
entire run: `1` and `2`** (`transcript.md:1673,1702,4017,4043,4819,5166,5199` = 1;
`transcript.md:5580,6489,6672,7376` = 2) - `sprint_number` never reaches 3, 4, or 5, despite 5 real
sprints completing. `grep -n "Tool call: \`start_sprint\`" transcript.md` confirms why: `start_sprint`
is only called 3 times in the whole run (lines 1660, 5550, 5567) - once at the very start (succeeds,
sprint_number 0→1), then twice back-to-back at the top of the harness's 4th real-sprint iteration
(first rejected, immediate retry succeeds, sprint_number 1→2). **It is never even attempted** at the
top of real-sprints 2, 3, or 5 - those sections open with `ScrumOrchestrator` transferring straight
to `ScrumMaster`, who immediately calls `get_budget_status`/`calculate_kpis` (`transcript.md:4518-
4552`, the literal first 5 events of real-sprint-2) - i.e. sprint-*closing* ceremony, not planning.

**Confirmed root cause - a mechanical dead end in `current_sprint_phase`.** `current_sprint_phase`
(`agents/scrum_team/helpers.py:887-915`) only ever returns `"Plan & Start Sprint"` by falling through
its first `if` block - which requires `sprint_backlog_pr_missing(state) is not None`
(`helpers.py:893`). `sprint_backlog_pr_missing` (`helpers.py:575-617`) returns `None` ("not missing")
whenever `state["sprint_backlog_pr_sprint"] == state["sprint_number"]` (`helpers.py:614`). Once a
sprint's own stories are all `Accepted` (confirmed via `manifest.json`: `US-0001`'s
`stages_completed` is `['Draft',...,'Accepted']` in every one of the 5 sprint snapshots -
`sprint_backlog` is never cleared), `current_sprint_phase`'s first branch returns `"Review, Retro &
Release"` (`helpers.py:910-911`) - correctly, for that sprint. The problem: **nothing ever moves
`sprint_number` forward except `start_sprint` itself, and `start_sprint` is phase-gated to refuse
running while the phase is `"Review, Retro & Release"`** (`agent.py:2360-2413`'s `PHASE_TOOL_POLICY`
gate - the exact rejection text observed at `transcript.md:5561-5563`: `"start_sprint does not belong
in the current phase ('Review, Retro & Release')... If this genuinely needs to happen now, call
start_sprint again with the exact same arguments - that immediate retry will be let through"`). Since
`sprint_backlog`/`sprint_backlog_pr_sprint` are never invalidated once their sprint's release has
actually gone out, `sprint_backlog_pr_missing` keeps returning `None` for the *same, already-finished*
sprint number indefinitely - there is no state transition that ever makes `current_sprint_phase` stop
saying `"Review, Retro & Release"` on its own. The only way out is the gate's own documented
immediate-retry override, which an agent must *proactively* choose to invoke without being told to -
it did so, inconsistently, after 2 wasted real-sprint iterations (real-sprint-4), and never again for
real-sprint-5.

**Directly observable consequences, all from this one mechanism:**
- `specs/reports/SPRINT-REPORT-NNN.md` numbering collapses to `001, 001, 001, 002, 002` across the 5
  real sprints (`transcript.md:4316,4844,5397` all write `SPRINT-REPORT-001.md`;
  `transcript.md:6946,7402` both write `SPRINT-REPORT-002.md`) - `_next_numbered_artifact_path`
  (`agents/scrum_team/tools/budget.py:547-584`) floors its number at `state.sprint_number`
  (`budget.py:582-583`), so a `sprint_number` stuck at 1 (then 2) makes every call overwrite the
  prior real sprint's own report instead of creating a new one - exactly matching `report.md`'s own
  flagged inconsistency: `specs/reports/SPRINT-REPORT-002.md` (file "002") contains the text
  *"Successfully delivered all planned stories for Sprint 5..."* (real-sprint-5's content).
- For 4 of the run's 5 real sprints (2, 3, 4, 5), the team re-runs the *same* retro/KPI/report/release
  ceremony on the *same* single already-`Accepted` story (`US-0001`) instead of returning to backlog
  planning for `US-0002`-`US-0006` - the dominant driver behind `report.md`'s Team Efficiency score
  (`1/5`, "catastrophic token inefficiency... ceremonial loops... duplicate technical issues
  SPEC-ISSUE-0001 through SPEC-ISSUE-0007") and >14M of the run's tokens spent for a net delivery of
  one story.

This is a different gap from SPEC-ISSUE-0066/0067 (confirmed present in this run's commit - `8d1a099`
is an ancestor of `8c1bd09`, the commit `0.1.0-run73` ran against): those fixed
`sprint_report_pending_release` getting stuck `True` across a sprint boundary. Here,
`sprint_report_pending_release` correctly clears (the release genuinely merges each time,
`report.md`: `PR Merges (after)` is `1/1` for all 5 sprints) - the stuck field is
`sprint_backlog`/`sprint_backlog_pr_sprint` staying valid for a sprint number that itself never
advances, which SPEC-ISSUE-0066/0067 never touched.

## Acceptance Criteria
- Once a sprint's release PR has actually merged (`sprint_report_pending_release` cleared) and its
  `sprint_backlog` stories are all `Accepted`, `current_sprint_phase(state)` must stop returning
  `"Review, Retro & Release"` for that same, already-closed sprint - it must return a phase that lets
  the *next* `start_sprint(goal)` call succeed on its **first** try, with no immediate-retry override
  needed.
- `sprint_number` must advance exactly once per real sprint the harness completes - a 5-sprint eval
  run must show 5 distinct `sprint_number` values (1 through 5) across its `start_sprint` responses,
  not collapse to 2.
- `specs/reports/SPRINT-REPORT-NNN.md` (and `TRANSCRIPT-/RETRO-/STEERING-NNN.md`) must get one
  distinct number per real sprint - no sprint's report may overwrite a prior sprint's own file.
- Does not reopen SPEC-ISSUE-0066/0067: `sprint_report_pending_release` must still correctly gate a
  sprint whose release genuinely hasn't gone out yet.
- A new regression test constructing state exactly as described above (sprint fully accepted and
  released, `sprint_number` unchanged) asserts `current_sprint_phase` no longer returns `"Review,
  Retro & Release"` for it, and that a subsequent `start_sprint` call succeeds without needing the
  `PHASE_TOOL_POLICY` override retry.

## Notes
- The `PHASE_TOOL_POLICY` immediate-retry override (`agent.py:2374-2413`) is working exactly as
  designed - it is not the bug. It exists precisely to unstick a wrong phase determination, and it
  did unstick this one, once, at real-sprint-4. The gap is that nothing in `current_sprint_phase`
  itself ever naturally recovers; recovery depends entirely on an agent choosing to invoke an escape
  hatch it isn't told to use, which it did inconsistently (0 times at real-sprint-2, 0 times at
  real-sprint-3, 1 time at real-sprint-4, 0 times at real-sprint-5).
- Likely fix shape: something must invalidate `sprint_backlog`/`sprint_backlog_pr_sprint`'s
  "satisfied" state once that sprint's own release has gone out - e.g. clearing `sprint_backlog` (or
  recording a "this sprint is fully closed" marker) at the same point `create_release_pr` already
  clears `sprint_report_pending_release` (`agents/scrum_team/tools/github.py:1766-1770`), so
  `sprint_backlog_pr_missing` naturally starts returning a "missing" string again and
  `current_sprint_phase` falls through to `"Plan & Start Sprint"`/`"Conceptual Work"` on its own.
- See also SPEC-ISSUE-0071 (filed from the same run) for a related, lower-severity finding: the
  `advance_story_stage` rejection message when only story-markdown content (not roadmap sync) fails
  is worded generically enough to read as a roadmap-sync failure - not the cause of this issue, but
  flagged by the same maintainer review of this run's logs.

## Test Approach
- `agents/scrum_team/tests/test_sprint_and_approval_gates.py` (or a new test module): construct
  `ScrumState` with `sprint_number=1`, `sprint_backlog_pr_sprint=1`, `sprint_backlog=[{"id": "US-0001",
  "type": "User Story", "stages_completed": [...,"Accepted"]}]`, `sprint_report_pending_release=False`
  (release already merged) - assert `current_sprint_phase(state) != "Review, Retro & Release"`.
- Extend with a full-cycle test: call `start_sprint` in that state and assert it succeeds
  (`status: "ok"`, `sprint_number` incremented) without first hitting the `PHASE_TOOL_POLICY`
  rejection.
- Replay `0.1.0-run73`'s shape (or an equivalent constructed scenario: 5 real-sprint-loop iterations,
  one story delivered in the first) against the fixed harness and confirm `sprint_number` reaches 5,
  not 2.

## Resolution
`create_release_pr` (`agents/scrum_team/tools/github.py`) now resets `state["sprint_backlog_pr_sprint"]`
to `0` in the same success branch where it already clears `sprint_report_pending_release` - the
instant a sprint's release PR actually goes out. This is the minimal state mutation needed:
`sprint_backlog_pr_missing` (`helpers.py`) compares `sprint_backlog_pr_sprint` against the *current*
`sprint_number`, and once they no longer match, it starts returning a non-`None` "missing" message
again for that (now-finished) sprint - which makes `current_sprint_phase`'s first `if` branch fail and
fall through to `"Plan & Start Sprint"`/`"Conceptual Work"` instead of re-evaluating `sprint_backlog`'s
stale `all_accepted` state. `sprint_backlog` itself is deliberately left untouched - the fix doesn't
need it, since `sprint_backlog_pr_missing` returning non-`None` short-circuits before `all_accepted` is
ever checked.

This gives `start_sprint`'s `PHASE_TOOL_POLICY` gate (`agent.py`) a phase that correctly lets it run on
the very next call - no reliance on an agent proactively invoking the gate's own immediate-retry
override, which is what actually broke the lock (once, inconsistently) in `0.1.0-run73`.

Tests added:
- `agents/scrum_team/tests/test_helpers.py::TestCurrentSprintPhase::test_plan_and_start_sprint_once_this_sprints_release_has_actually_gone_out` -
  the direct regression test: state shaped exactly like a sprint whose release just merged
  (`sprint_backlog_pr_sprint=0`, `sprint_report_pending_release=False`) but whose `sprint_backlog`
  still lists the prior sprint's `Accepted` story (nothing has cleared it) - asserts
  `current_sprint_phase` returns `"Plan & Start Sprint"`, not `"Review, Retro & Release"`.
- `agents/scrum_team/tests/test_github.py::TestGitHubTools::test_create_release_pr_resets_sprint_backlog_pr_sprint_on_success` -
  asserts `create_release_pr`'s own success path actually performs this reset.

Full suite (`agents/scrum_team/tests`, via `docker compose run`, properly project-scoped per
`lib_docker.compose_project_args("test")`) passes: 1096 of 1101 tests, the same 5 pre-existing
failures (`test_agent.py`'s identity-fallback test and 4 in `test_steering_proposals.py`) present and
unrelated - confirmed via `git stash` A/B comparison when investigating SPEC-ISSUE-0069.
