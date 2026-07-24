# Issue

- Issue ID: ISSUE-0001
- Title: Human Review Gates Are Not Mechanically Enforced
- Status: Done
- Priority: Must
- Owner: Scrum Team
- Last Updated: 2026-07-24

## Overview
`ORCHESTRATOR_PROMPT` states three separate human-in-the-loop MANDATORY rules: "A sprint can ONLY
start after explicit human review and approval of the sprint goal and sprint backlog" (line 46),
"Human Review is mandatory for each sprint increment" (line 45), and PO's "Ensure Human Review is
done for each increment" (line 184, repeated at line 279 for Scrum Master). None of these are
backed by any code check. There is no state field recording that a human approved anything, and no
tool refuses to proceed in the absence of one. In a fully autonomous eval run (the harness this
project actually exercises via `run_eval.py`), these three "MANDATORY" human checkpoints are
therefore never true even once - the rule is satisfied purely by the model asserting it in
conversation text.

## Acceptance Criteria
- A `human_approvals` (or similar) field exists in `ScrumState` recording explicit approval events
  (sprint goal/backlog, release increment) with a timestamp/actor.
- `plan_sprint_backlog_item`/whatever starts a sprint refuses (mirroring `blocks_direct_status_set`'s
  error-return pattern) until the current sprint's goal/backlog has a recorded approval.
- `create_release_pr` (or `create_sprint_report`) similarly refuses without a recorded increment
  approval.
- A test exists that calls the gated tool with no approval recorded and asserts `status == "error"`,
  then records an approval and asserts the same call now succeeds.

## Notes
- Where the gap currently lives: no file implements this at all - grep for
  `human_approv|approval` across `agents/scrum_team/tools/*.py` and `agents/scrum_team/state.py`
  turns up nothing outside the prompt text itself.
- In a real human-operated run this is presumably satisfied out-of-band (a person literally reading
  the PR/report before merging), but the prompt frames it as something the *agent* must ensure, and
  nothing stops an eval run from silently "ensuring" it by just saying so.
- Traces back to `agents/scrum_team/prompts.py` lines 45-46, 184, 279.

## Test Approach
- Unit test on the new gate function directly (no approval -> error; approval recorded -> ok),
  following the existing pattern in `test_budget.py`'s
  `test_create_sprint_report_rejects_without_new_retro_or_impediment`.

## Resolution
- Added `human_approvals` (list), `sprint_approval_baseline`, and `release_approval_baseline` to
  `ScrumState`, plus a new `record_human_approval(approval_type, note)` tool
  (`agents/scrum_team/tools/scrum.py`) for `"sprint"`/`"release"` approvals.
- `advance_story_stage(..., "Implemented")` (`agents/scrum_team/tools/requirements.py`) now refuses
  unless a fresh `"sprint"` approval has been recorded since the last sprint report closed.
- `create_release_pr` (`agents/scrum_team/tools/github.py`) now refuses unless a fresh `"release"`
  approval has been recorded since the last successful release PR.
- Both baselines are bumped only on success (mirroring `retro_baseline`), so the same approval can't
  be reused to unblock more than one sprint/release.
- Wired into `PO_PROMPT`, `SM_PROMPT`, and the tool lists for ProductOwner/ScrumMaster/QualityGuardian.
- Tests: `test_scrum.py::test_record_human_approval`,
  `test_requirements.py::TestAdvanceStoryStageGates::test_implemented_requires_fresh_sprint_approval`,
  `test_github.py::test_create_release_pr_rejects_without_fresh_release_approval`.
