# Issue

- Issue ID: ISSUE-0043
- Title: Accepted Stage Has No Approval-Side Verification Gate
- Status: Draft
- Priority: Should
- Owner: Architect
- Last Updated: 2026-08-06

## Overview
Found while investigating and fixing the "a review can only be denied with a concrete reason"
requirement (see `deny_review`, `agents/scrum_team/tools/requirements.py`, RELEASE.md "Denying a
review"). That fix covers the *rejection* side of Reviewed/Tested/Accepted uniformly. But the
*approval* side is not symmetric across the three: `advance_story_stage`'s Reviewed gate requires a
fresh `gh_pr_review`/`gh_pr_comment` from Architect since the last story was Reviewed
(`architect_review_baseline`), and the Tested gate requires the same from QA plus a real, passing
`check_build()`/test run. **Accepted has no such check at all** - `advance_story_stage(id,
"Accepted")` only runs the generic ordering/ownership checks every stage gets; there is no
Accepted-specific `elif` branch verifying any real activity happened. A Product Owner can currently
call `advance_story_stage(id, "Accepted")` immediately after Tested with zero mechanical evidence
that acceptance criteria were actually checked against anything.

`deny_review` now makes a *denial* require a real reason - but if Product Owner never has to prove
an approval was based on anything either, the "acceptance is a real checkpoint, not a rubber stamp"
intent stated in `PO_PROMPT` (and now docs/DEVELOPMENT-WORKFLOW.md's `AcceptGate`) still rests
entirely on the model's own judgment, the same "prompt-only, not enforced in code" gap this whole
project keeps finding and closing for other stages.

## Acceptance Criteria
- `advance_story_stage(title_or_id, "Accepted")` requires some mechanical evidence that Product
  Owner actually performed an acceptance check for *this* story since it was last Tested - exactly
  what that evidence should be is the open design question (see Notes).
- A test exists that would fail today (Accepted succeeds with zero PO-side activity recorded since
  Tested) and pass once fixed.
- Whatever mechanism is chosen must not force Product Owner to adopt an unrelated tool (e.g.
  `gh_pr_review`, which isn't in PO's own tool list and isn't a natural fit for a business-level
  acceptance judgment, unlike Architect's/QA's code-level reviews).

## Notes
- Candidate approaches to weigh, not yet decided: (a) a new lightweight
  `record_acceptance_check(title_or_id, note)` tool mirroring `record_design_approval`'s per-story
  flag pattern; (b) requiring `deny_review`'s counterpart - i.e. an explicit `approve_review`-style
  call - for all three review stages, making approval as explicit an action as denial now is; (c)
  reusing `record_human_approval`'s existing "sprint"/"release"/"budget" gate machinery with a new
  approval type. Each has different implications for how much friction it adds at levels where
  Product Owner IS the human (Product level) vs. where a real human reviews afterward.
  (b) is the most consistent with the shape `deny_review` just established (a real action for the
  outcome that's actually happening) and is the front-runner, but changes the shape of every
  Reviewed/Tested/Accepted approval, not just Accepted's - worth confirming that's wanted before
  committing to it project-wide vs. an Accepted-only fix.
- Distinct from `deny_review` (this ISSUE, fixed): that ensures a *denial* is concrete; this is
  about whether an *approval* has any mechanical backing at all for Accepted specifically.

## Test Approach
- Unit test on `advance_story_stage(..., "Accepted")` asserting it's rejected without whatever new
  evidence requirement is chosen, mirroring the existing Reviewed/Tested gate tests in
  `agents/scrum_team/tests/test_requirements.py::TestAdvanceStoryStageGates`.
