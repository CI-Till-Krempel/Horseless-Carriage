# Issue

- Issue ID: ISSUE-0006
- Title: git_push Has No Protected-Branch Guard
- Status: Done
- Priority: Must
- Owner: Scrum Team
- Last Updated: 2026-07-24

## Overview
`DEV_PROMPT` (MANDATORY, line 345): "The repository's configured default branch is PROTECTED - you
CANNOT push to it directly... All changes must be made via feature branches and Pull Requests." But
`git_push(branch, ...)` (`agents/scrum_team/tools/github.py`) accepts any `branch` string and pushes
to it unconditionally - nothing compares `branch` against the repo's configured default branch
(`repo.default_branch` in state, surfaced via `repo_status`) and refuses the call. The entire
protection is prompt-only: a model that (incorrectly) decides to push straight to `main` (or whatever
the configured default is) faces no code-level obstacle at all.

## Acceptance Criteria
- `git_push` refuses (returns `{"status": "error", ...}` without running any git commands) when the
  requested `branch` (after `_with_eval_branch_prefix` normalization) equals the configured default
  branch.
- Rejection message names the actual configured default branch and points at `gh_pr_create`/
  `create_release_pr` as the correct path, matching the prompt's own guidance not to assume the
  default is literally `main`.
- A test exists: `git_push("main", ...)` (or whatever `GITHUB_REPO_BRANCH`/`repo.default_branch` is
  configured to) errors without touching the filesystem/subprocess; `git_push("feature/x", ...)`
  proceeds normally.

## Notes
- Where the gap currently lives: `git_push` in `agents/scrum_team/tools/github.py` (starts around
  line 93) has no branch-name comparison at all before running `git checkout -B`/`git push`.
- The configured default branch is already available via `_configured_repo_root`/state's `repo`
  dict (see `repo_status`) - the fix is a lookup + comparison, not new state.
- Traces back to `agents/scrum_team/prompts.py` line 345 (`DEV_PROMPT`).

## Test Approach
- Unit test mocking `_run` (or asserting it's never called) when `branch` matches the configured
  default, plus a normal-path test confirming feature branches are unaffected.
