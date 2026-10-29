# Issue

- Issue ID: SPEC-ISSUE-0076
- Title: A Retro-Filed Must Issue Never Actually Jumps The Queue - `_resort_backlog_by_priority`'s Stable Sort Leaves It Stuck Behind Already-Queued Same-Priority Work, Defeating GH #368's Ordering Gate
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-10-29

## Overview
Investigated `0.1.0-run76` (5/5 sprints completed). `report.md`'s Requirements Quality finding: *"the
team repeatedly filed recursive self-referential process issues (e.g., SPEC-ISSUE-0001 through
SPEC-ISSUE-0004) to address local test runs rather than focusing strictly on product features"*
(Top Problem #3, severity: medium).

**Surface symptom.** Every sprint's retrospective filed a Must-priority process Issue, nearly
identically worded each time: SPEC-ISSUE-0001 ("Ensure QA checks test suite run locally before marking
Tested..."), SPEC-ISSUE-0002 ("...to catch integration/ID mismatches early"), SPEC-ISSUE-0003 ("Ensure
QA and Dev team run local test suite prior to review..."), SPEC-ISSUE-0004 ("Ensure test fixtures and
dependencies align with existing code structure...") - four near-duplicates across five sprints
(`manifest.json`: each filed `type: Issue, priority: Must`), because the same underlying problem was
never actually fixed - each sprint's retrospective rediscovered it fresh. `report.md`'s KPI table
corroborates zero resolution: "Issues fixed" reads `0, 0, 0, 0, 0` across all five sprints, and by
Sprint 5 SPEC-ISSUE-0001/0002/0003 all still sit at exactly `['Draft', 'Ready']` - never `Implemented`.

**Confirmed mechanism.** `_preceding_story` (`agents/scrum_team/tools/requirements.py:1324-1376`) is
GH #368's own fix for exactly this failure class - its docstring explicitly documents that an Issue is
*deliberately* NOT exempted from the one-story-at-a-time ordering gate `start_feature_branch` enforces,
specifically so a Ready Must-priority Issue mechanically blocks all lower-priority work until it
reaches `Accepted`, rather than silently sitting "merely planned." This mechanism genuinely works when
an Issue's array position actually precedes the team's current frontier of work -
`test_a_preceding_issue_still_blocks_but_names_the_escape_hatch` (`test_github.py`) already proves it.

The bug is upstream of that gate, in how a retro-filed Issue gets positioned in `product_backlog` in
the first place. `_file_retro_items_as_issues` (`agents/scrum_team/tools/budget.py:765-818`) calls
`upsert_issue` -> `upsert_backlog_item`, which always **appends** a new item
(`agents/scrum_team/tools/requirements.py:837`, `backlog.append(item)`), then `set_priority(issue_id,
"Must", ...)` - which re-sorts `product_backlog` by MoSCoW rank via `_resort_backlog_by_priority`
(`requirements.py:329-334`), **a deliberately stable sort**: *"stable so items sharing a priority keep
their existing relative order"* (confirmed intentional, and relied on by
`test_same_priority_items_keep_relative_order`/`test_set_priority_moves_item_ahead_of_lower_priority_ones`
in `test_requirements.py`). Since the sprint's own User Stories (US-0002 through US-0006) were already
in `product_backlog` at `Must` priority *before* the retro ever ran, a newly-filed, newly-`Must`-ed
Issue appended after them and then stably re-sorted **stays exactly where it landed - last among equal-
priority items** - never actually becoming any story's "immediately preceding" item the way GH #368's
design requires. `create_sprint_backlog_pr`'s `unaddressed_must_issues` check
(`github.py:985-1004`) does correctly force the Issue to `Ready` each sprint (confirmed firing for
SPEC-ISSUE-0001 at `transcript.md:4117`, SPEC-ISSUE-0002 at `7154`, SPEC-ISSUE-0003 at `14008`) - but
`Ready` is as far as it can mechanically get, because the ordering gate that's supposed to force it the
rest of the way to `Accepted` never actually sees it as blocking anything.

This is a reopened instance of a gap SPEC-ISSUE-0072's own Notes section predicted ("worth a follow-up
issue if a future run shows a role actually getting stuck on it") - confirmed here with the precise
mechanical root cause: not that a role is structurally unreachable, but that the backlog-positioning
step the existing ordering-gate design depends on was never actually performed for this specific,
auto-filed, auto-prioritized path.

## Acceptance Criteria
- A retro-filed Issue that gets auto-prioritized `Must` (`_file_retro_items_as_issues`'s
  non-`"Product"`-interaction-level path) must end up positioned ahead of the team's remaining
  (not-yet-`Accepted`) `product_backlog` work immediately - not merely "somewhere in the Must band" -
  so `_preceding_story`'s existing ordering gate genuinely blocks on it per GH #368's design.
- Must not disturb already-`Accepted` items' positions (they're done; reordering behind a new Issue
  would be meaningless) or `_resort_backlog_by_priority`'s own general stable-sort behavior for every
  other caller (`set_priority`/`upsert_backlog_item` reprioritizing an existing item) - this is a
  narrow, additional repositioning for this one auto-filing path, not a change to sort semantics.
- `create_sprint_backlog_pr`'s rejection message (`github.py:985-1004`) must not continue directing
  ProductOwner toward `plan_sprint_backlog_item`, a tool it cannot call - reword it to describe what the
  Issue's correct positioning now mechanically does instead.
- A new regression test replaying this run's shape: a Must-priority Issue auto-filed after already-
  queued, same-priority stories - assert it ends up ahead of them, and that `start_feature_branch` on
  one of those stories is genuinely refused until the Issue is resolved.

## Notes
- Distinguish this from "ScrumMaster/ProductOwner having a bad retro" (Step 4 of the investigation
  process): the retro content itself was reasonable each time (a real, recurring QA/test-integration
  pain point, not fabricated filler) - the gap is structural, not a quality-of-reflection problem.
- `plan_sprint_backlog_item` being DevTeam-exclusive (this issue's original working hypothesis) is real
  but is not the load-bearing mechanism - `create_sprint_backlog_pr`'s own gate only ever required
  `Ready`, not `sprint_backlog` membership, so ProductOwner was never actually blocked on that tool. The
  real gap was one layer further downstream, in `_preceding_story` never seeing the Issue at all.
- See also: SPEC-ISSUE-0001 through SPEC-ISSUE-0004 as filed inside the eval run's own target repo
  (`CI-Till-Krempel/horseless-carriage-eval-todo-app`, not this repo) are themselves evidence artifacts
  of this run, not real tracked issues in this codebase - referenced here only as the data this
  investigation is based on.

## Test Approach
- `agents/scrum_team/tests/test_requirements.py`: unit-test the new repositioning helper directly -
  moves ahead of same-priority not-yet-`Accepted` items, does not move ahead of already-`Accepted`
  ones, skips Epics when finding the frontier, appends at the end when everything else is `Accepted`.
- `agents/scrum_team/tests/test_budget.py` (`TestFileRetroItemsAsIssues`): an auto-prioritized Must
  Issue filed with already-queued, same-priority stories already in `product_backlog` ends up ahead of
  them.
- `agents/scrum_team/tests/test_github.py` (`TestStartFeatureBranchOrderingGate`): end-to-end - file a
  retro Issue through the real function, then confirm `start_feature_branch` on an already-queued,
  lower-priority-position story is genuinely refused, naming the Issue.

## Resolution
Added `_move_item_to_front_of_remaining_work` (`agents/scrum_team/tools/requirements.py`) - repositions
a backlog item to sit immediately before the first non-Epic, not-yet-`Accepted` item in
`product_backlog` (appending at the end if everything else is already `Accepted`), rather than the
front of the whole historical backlog `_resort_backlog_by_priority` alone would produce. Called once,
from `_file_retro_items_as_issues` (`agents/scrum_team/tools/budget.py`), immediately after
`set_priority(issue_id, "Must", ...)` succeeds on the auto-prioritization path - its position then stays
put, for the same reason the stable sort doesn't disturb it afterward.

Deliberately did **not** change `_resort_backlog_by_priority`'s own stable-sort behavior, which is
relied on (and explicitly tested) by every other caller - this is a narrow, additional repositioning
specific to the one auto-filing path that was actually broken, not a change to general sort semantics.

Reworded `create_sprint_backlog_pr`'s `unaddressed_must_issues` rejection message (`github.py`) to stop
directing ProductOwner at `plan_sprint_backlog_item` (a tool it cannot call) and instead explain that a
Ready Must Issue is already mechanically positioned to block the rest of the backlog via the ordering
gate - there is nothing further for ProductOwner itself to schedule.

Tests added:
- `agents/scrum_team/tests/test_requirements.py` (`TestMoveItemToFrontOfRemainingWork`): the
  repositioning helper in isolation - moves ahead of same-priority not-yet-Accepted items, does not
  move ahead of already-Accepted ones, skips Epics when finding the frontier, appends at the end when
  everything else is Accepted, no-ops for an unknown item ID.
- `agents/scrum_team/tests/test_budget.py`
  (`test_auto_prioritized_must_issue_jumps_ahead_of_already_queued_same_priority_work`): the direct
  regression test - replays `0.1.0-run76`'s exact shape via the real `_file_retro_items_as_issues`.
- `agents/scrum_team/tests/test_github.py`
  (`test_a_freshly_auto_filed_retro_issue_blocks_a_lower_priority_story`): end-to-end - files the Issue
  through the real function, then confirms `start_feature_branch` on an already-queued story is
  genuinely refused, closing the loop with the already-passing
  `test_a_preceding_issue_still_blocks_but_names_the_escape_hatch` (which proves the downstream gate
  itself was never the problem).

Full suite (`agents/scrum_team/tests`, via `docker compose run`, project-scoped per
`lib_docker.compose_project_args("test")`) passes 1112/1117 - the same 5 pre-existing failures already
tracked (fixed by SPEC-ISSUE-0074's companion conftest.py change, landing in a separate PR) present and
unrelated to this change.
