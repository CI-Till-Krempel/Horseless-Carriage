# User Story

- Story ID: US-0010
- Title: Verify Release PR Diff Against Sprint Tracking
- Status: Draft
- Priority: Must
- Owner: Product Owner
- Last Updated: 2026-07-16

## As a Product Owner, I want `create_release_pr` to diff the actual git changes against tracked sprint files and warn/fail on mismatch, so completed work is never silently dropped.

## Acceptance Criteria
- Given `sprint_files_touched` (per US-0009) and the actual `git diff --name-only`/`git status --porcelain` output at release time, when `create_release_pr()` runs, then it compares the two sets before pushing.
- Given a mismatch (tracked files not in the diff, or vice versa), when detected, then a clear warning/error is surfaced rather than silently proceeding.
- Edge case: the two sets match exactly — the release proceeds with no warning noise.

## Notes
- Parent epic: EP-0003.
- Design/technical notes: touch point is `create_release_pr()`/`git_push()` in `agents/scrum_team/tools/github.py`, which currently just calls `git_push(add_all=True)` with no verification step.

## Test Approach
- Unit test with mocked git output matching `sprint_files_touched` exactly — no warning raised.
- Unit test with a deliberate mismatch (extra or missing file) — assert the warning/failure path triggers.
