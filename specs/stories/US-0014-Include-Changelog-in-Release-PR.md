# User Story

- Story ID: US-0014
- Title: Include Changelog in Release PR
- Status: Draft
- Priority: Should
- Owner: Dev Team
- Last Updated: 2026-07-16

## As a Dev Team member, I want the generated changelog diff bundled into the same release PR, so changelog updates ship atomically with code.

## Acceptance Criteria
- Given a changelog entry generated per US-0012/US-0013, when `create_release_pr()` runs, then the `CHANGELOG.md` change is part of the same commit/PR as the code changes, not a separate follow-up.
- Given US-0009's `sprint_files_touched` tracking, when the changelog file is written, then it's recorded there too, so US-0010's diff verification doesn't flag it as unexpected.
- Edge case: changelog generation fails — the release PR still proceeds with the code changes, surfacing a clear warning that the changelog step was skipped, rather than blocking the whole release.

## Notes
- Parent epic: EP-0004. Depends on EP-0003 (US-0009/US-0010) for the release-PR flow this plugs into.
- Design/technical notes: wire the changelog-generation call into `create_release_pr()` in `agents/scrum_team/tools/github.py`, before the `git_push` call.

## Test Approach
- Integration test asserting `CHANGELOG.md` changes appear in the same PR diff as other release changes.
- Unit test for the changelog-generation-failure path, asserting the release still proceeds with a warning.
