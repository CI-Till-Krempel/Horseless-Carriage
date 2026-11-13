# Issue

- Issue ID: SPEC-ISSUE-0091
- Title: Every Retro-Filed Issue Jumps The Queue As "Must", Even When It's Not A Blocker
- Status: Ready
- Priority: Should
- Owner: Architect
- Last Updated: 2026-11-13

## Overview
A real eval run (0.2.1-run94) scored Team Efficiency 1/5: "Sprints 3, 4, and 5 accomplished zero
user-facing feature work, instead getting trapped in recursive retro actions, security scan triage
issues ... and endless PR reviews." Direct evidence of why: in sprint 2, the moment ProductOwner
tried to publish the sprint backlog, `create_sprint_backlog_pr` refused outright
(`transcript.md:5181-5182`) because a retro-filed Issue (`SPEC-ISSUE-0001`, a routine "security scan
findings need triage" advisory, not a real blocker) had been auto-filed at "Must" priority and
mechanically parked at the front of the one-story-at-a-time queue. ProductOwner's only way forward
was to manually override it: `plan_backlog_item(priority="Should", title_or_id="SPEC-ISSUE-0001",
priority_justification="Demoting from Must to Should as it is an advisory retro finding/issue rather
than a blocker to the sprint functional roadmap.")` (`transcript.md:5188-5191`). Every sprint that
files a routine retro/process finding forces ProductOwner through this same reactive fight just to
get back to shipping stories - the mechanism SPEC-ISSUE-0076 built to stop a genuine blocker from
being silently starved now equally strangles every non-blocking finding too.

**Confirmed mechanism.** `_file_retro_items_as_issues` (`agents/scrum_team/tools/budget.py:799-852`)
auto-assigns `"Must"` priority to **every** newly-filed `category="technical"` retro/impediment
finding, unconditionally, at every interaction level except `"Product"` - and (unless
`recurs_since_sprint` is set, SPEC-ISSUE-0081's unrelated livelock guard) also calls
`_move_item_to_front_of_remaining_work` to park it ahead of the team's actual remaining
(not-yet-Accepted) work, so the one-story-at-a-time ordering gate (`_preceding_story`,
`requirements.py:1585`) blocks `start_feature_branch` on every other story until it's resolved. There
is no distinction anywhere in this path between "this genuinely blocks further sprint work right now"
and "this is a routine process/tooling gap worth tracking but not urgent" - both get treated
identically.

The data needed to make that distinction already exists and is already being collected, but is
silently discarded: `add_retro_action`/`add_impediment` (`agents/scrum_team/tools/scrum.py:883,955`)
both take a `priority: str = "normal"` parameter ("normal" | "high"), and ScrumMaster is already
expected to set it meaningfully - but only for `category="human"` escalation
(`ScrumMaster-workflow.md:173-178`: "Set `priority='high'` only when continuing without an answer
would waste real further work"). For `category="technical"`, the stored dict literal never even
persists the argument: `add_impediment`'s `imp = {...}` (`scrum.py:931-934`) and `add_retro_action`'s
`entry = {...}` (`scrum.py:1004-1011`) both omit `"priority"` entirely, so by the time
`_file_retro_items_as_issues` runs (often much later, at `create_sprint_report` time -
`budget.py:1421`), the signal is already gone even if ScrumMaster had tried to set it.

## Acceptance Criteria
- `add_retro_action`/`add_impediment` persist their `priority` argument onto the stored
  `retro_actions`/`impediment_log` entry (not just use it transiently for the `category="human"`
  escalation path).
- `_file_retro_items_as_issues` only auto-assigns `"Must"` priority + the front-of-queue placement
  (`_move_item_to_front_of_remaining_work`) to a `category="technical"` finding whose entry has
  `priority == "high"` (a genuine current-sprint blocker, exactly SPEC-ISSUE-0076's original intent).
  The default `priority="normal"` still files a real, trackable, plannable Issue (not silently
  dropped - GH issue #164's own requirement stands) but at `"Should"` priority with no front-queue
  jump, so it naturally sorts behind this sprint's already-planned `"Must"`-priority stories instead
  of displacing them.
- SPEC-ISSUE-0081's recurring-finding carve-out (don't let a confirmed restatement jump the queue
  even when Must) continues to apply only to the now-narrower "blocking" path.
- `ScrumMaster-workflow.md`'s TRIAGE section documents that `priority="high"` also marks a
  `"technical"` finding as blocking (Must + front-of-queue), not just the existing `category="human"`
  meaning - with guidance on when a technical finding actually qualifies (blocks the build/tests/
  other stories right now) versus the common case (a routine process/tooling gap, left at the
  default `"normal"`).
- `ProductOwner-workflow.md`'s BACKLOG GROOMING section documents the new default behavior (retro
  Issues normally land at `"Should"`, behind this sprint's stories, picked up through the same
  `list_open_issues` grooming loop) and that PO can still escalate one to `"Must"`
  (`set_priority`) if grooming reveals ScrumMaster under-triaged a genuine blocker.
- A test exists: a `category="technical"` entry with no explicit `priority` (or `priority="normal"`)
  files as `"Should"` and does NOT precede already-queued `"Must"`-priority stories in
  `product_backlog`; the same entry with `priority="high"` still files as `"Must"` and still jumps
  the queue exactly as SPEC-ISSUE-0076 established.

## Notes
- Root cause has two parts, both required for the fix: (1) `scrum.py:931-934`/`scrum.py:1004-1011`
  drop the `priority` argument instead of storing it, and (2) `budget.py:821-852` never reads it even
  where it is available (the `recurs_since_sprint` check already reads a different field off the same
  `entry`, so the omission isn't a missing capability, just a decision that was never made).
- Deliberately scoped to retro/impediment-filed Issues only (GH issue #164's `_file_retro_items_as_
  issues`), not `run_release_audit`'s advisory findings (`agents/scrum_team/tools/quality.py:525-589`,
  SPEC-ISSUE-0090's subject) - that path already auto-sets `"Must"` without an explicit front-queue
  jump call, a materially different (and separately scoped) mechanism not covered by this Issue.
- `create_sprint_backlog_pr`'s `unaddressed_must_issues` gate (`agents/scrum_team/tools/github.py:
  1110-1134`) needs no code change: it already only fires on `priority == "Must"`, so once non-
  blocking findings stop being auto-"Must", they stop tripping this gate too - the existing gate text
  ("already mechanically positioned at the front of the one-story-at-a-time ordering queue") stays
  accurate once only genuine blockers reach it.

## Test Approach
- Unit tests in `agents/scrum_team/tests/test_scrum.py`: `add_retro_action`/`add_impediment` called
  with `category="technical"` and each of `priority="normal"` (default) / `priority="high"`; assert
  the stored entry includes `"priority"` matching what was passed.
- Unit tests in `agents/scrum_team/tests/test_budget.py` (`TestFileRetroItemsAsIssues`): update the
  existing default-case test to assert `"Should"`, not `"Must"`; update the front-queue-jump tests to
  set `priority="high"` on the entry (since jumping is now conditional on it); add a new test for a
  `priority="normal"` finding filed alongside already-queued `"Must"` stories, asserting it lands
  *after* them in `product_backlog`, not before.
