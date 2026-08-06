# Issue

- Issue ID: ISSUE-0044
- Title: A Denial Does Not Block A Still-Eligible Advance
- Status: Done
- Priority: Could
- Owner: Architect
- Last Updated: 2026-08-06

## Overview
Found while writing `agents/scrum_team/tests/test_story_pipeline_state_machine.py` (a scripted,
no-LLM test driving the documented pipeline end to end via the real tool functions in the exact
order a well-behaved conversation would use). That test only ever scripts the *correct* order
(deny → fix → fresh review → advance), and passes. But while designing it, a real, adjacent gap
surfaced: `deny_review` (see `agents/scrum_team/tools/requirements.py`) records a story's
`review_denial`, but does **not** itself block a subsequent `advance_story_stage` call for that same
stage. The Reviewed/Tested gates' own evidence check (`pr_review_calls[role] >
{architect,qa}_review_baseline`) is a coarse, sprint-wide counter, not scoped to one story - so if
that counter already happens to be satisfied (e.g. from the very same `gh_pr_review` call that
prompted the denial in the first place, or from a different story's review earlier in the sprint),
calling `advance_story_stage(id, stage)` immediately after `deny_review(id, stage, reason)` would
currently still succeed, silently overwriting the denial's intent. Accepted has no evidence check at
all (ISSUE-0043), so this applies there unconditionally.

This is **not** exercised by the current tests (which only script the well-behaved order), and isn't
necessarily exploitable by a well-behaved reviewer either - `advance_story_stage`'s own ownership
check means only the *same* role that denied can also advance that stage, and calling
`advance_story_stage` is already this codebase's established mechanical form of "I approve this," so
a reviewer calling both in the same breath is already self-contradictory, not something the tooling
can distinguish from "I changed my mind after re-reviewing, right now, and that's fine." Whether this
is worth hardening depends on how much weight to put on "prevent a self-contradictory sequence" vs.
"trust the same role's own subsequent judgment," given `deny_review` already guarantees the reason
itself is real (see RELEASE.md "Denying a review").

## Acceptance Criteria
- Decide (not yet decided - see Notes) whether a per-story `review_denial` for a given stage should
  mechanically block that exact stage's `advance_story_stage` call until some fresh, denial-specific
  signal fires - not just the existing sprint-wide review-call counter.
- If yes: `advance_story_stage` refuses to complete a stage while that story has an *unresolved*
  `review_denial` for it, with a clear path to actually resolve one (must not create a deadlock -
  see Notes on why a naive "block until resolved, only resolved by advancing" design deadlocks).
- A test exists demonstrating the chosen behavior (blocked when unresolved, succeeds once resolved)
  for at least one of Reviewed/Tested; Accepted needs its own design given ISSUE-0043's related gap.

## Notes
- A deadlock trap to avoid: if `advance_story_stage` refuses outright while `review_denial` is set,
  and the *only* way to clear `review_denial` is a successful `advance_story_stage` call, the story
  can never move again. Any fix needs an independent, explicit resolution signal distinct from the
  gate it protects - e.g. snapshotting the review-call counter's value at deny time
  (`pr_review_calls[role]` when `deny_review` runs) and requiring the counter to have grown *past*
  that snapshot before the same stage can complete, so a genuinely fresh review is required, not
  just re-use of the review that led to the denial. This doesn't extend to Accepted at all today,
  since PO has no review-call counter to snapshot (see ISSUE-0043) - resolving that gap first would
  likely need to happen before or alongside this one.
- Distinct from ISSUE-0043 (Accepted has no *approval*-side evidence gate at all) - this is about
  whether a *denial*, once recorded, has any teeth against an otherwise-still-eligible advance call,
  for stages that DO have some evidence gate.

## Test Approach
- Unit test on `advance_story_stage`: record a `review_denial` for "Reviewed" without any *new*
  `pr_review_calls["Architect"]` increment since the denial, and assert the stage transition is
  refused (once a design is chosen) - today it currently succeeds if the counter condition happens
  to already be met, which is the gap this issue tracks.

## Resolution
Went with the snapshot approach from Notes: `deny_review` now records
`review_denial["review_count_at_denial"] = pr_review_calls[role]` at the moment of denial, for
Reviewed/Tested (`counter_key = {"Reviewed": "Architect", "Tested": "QA"}.get(stage)`).
`advance_story_stage`'s Reviewed/Tested gates now additionally refuse if
`{architect,qa}_review_count <= review_denial["review_count_at_denial"]` - i.e. the sprint-wide
counter hasn't grown *past* the value it had when this story was denied, meaning no genuinely new
review has happened since. This avoids the deadlock trap: resolution is a normal, pre-existing action
(a fresh `gh_pr_review`/`gh_pr_comment`), not gated behind the thing it's unblocking.

**Accepted is now covered too, once ISSUE-0043 gave it a per-story counter to snapshot against.**
ISSUE-0043 added `record_acceptance_check`/`acceptance_check_count` as Accepted's evidence gate;
`deny_review` snapshots that counter instead
(`review_denial["acceptance_count_at_denial"] = acceptance_check_count`) when denying Accepted, and
`advance_story_stage`'s Accepted branch refuses if `acceptance_check_count <=
review_denial["acceptance_count_at_denial"]` - a fresh `record_acceptance_check` call, not a reuse of
the one that led to the denial, is required to resolve it. Same mechanism, same deadlock-avoidance
shape as Reviewed/Tested, just against Accepted's own counter instead of a `pr_review_calls` role.

- `agents/scrum_team/tools/requirements.py`: `deny_review` snapshots the counter for
  Reviewed/Tested/Accepted; `advance_story_stage`'s Reviewed/Tested/Accepted `elif` branches check
  the snapshot.
- `agents/scrum_team/tests/test_requirements.py`:
  `test_denial_blocks_advance_even_if_the_sprintwide_counter_already_passes`,
  `test_tested_denial_blocks_advance_even_if_the_sprintwide_counter_already_passes`, and
  `test_accepted_denial_blocks_advance_even_if_already_checked_once` - each constructs the exact
  regression this issue tracks (evidence already satisfied *before* the denial) and confirms it's now
  refused, then confirms a fresh signal after the denial resolves it.
- RELEASE.md "Denying a review" updated with the mechanics.
