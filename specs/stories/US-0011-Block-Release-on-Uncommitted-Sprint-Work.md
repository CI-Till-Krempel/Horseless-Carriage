# User Story

- Story ID: US-0011
- Title: Block Release on Uncommitted Sprint Work
- Status: Draft
- Priority: Should
- Owner: Dev Team
- Last Updated: 2026-07-16

## As a Dev Team member, I want uncommitted/untracked sprint-related files detected before the PR opens, so `add_all=True` can't silently miss anything.

## Acceptance Criteria
- Given uncommitted changes exist in the target repo's working tree at release time, when `create_release_pr()` is invoked, then those changes are detected and included (or explicitly flagged) before the PR is opened.
- Given untracked files that match `sprint_files_touched`, when detected, then they're staged/committed rather than left behind.
- Edge case: uncommitted changes exist that are unrelated to this sprint (e.g. stray local edits) — these are flagged for human review rather than auto-committed blindly.

## Notes
- Parent epic: EP-0003.
- Design/technical notes: complements US-0010 — where US-0010 checks the diff *after* push, this story catches the working-tree state *before* `git_push` runs, closing the gap `add_all=True` currently papers over silently.

## Test Approach
- Unit test with staged-but-uncommitted sprint files, asserting they get included.
- Unit test with unrelated stray changes present, asserting they're flagged rather than silently swept into the release.
