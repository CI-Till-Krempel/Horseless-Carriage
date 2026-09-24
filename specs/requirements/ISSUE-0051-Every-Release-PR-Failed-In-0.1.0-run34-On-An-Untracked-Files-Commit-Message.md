# Issue

- Issue ID: ISSUE-0051
- Title: Every Release PR Attempt Failed In `0.1.0-run34` Because `_git_push_impl` Only Recognized One Of Git's Two "Nothing To Commit" Messages
- Status: Done
- Priority: Must
- Owner: DevTeam
- Last Updated: 2026-09-24

## Overview
Reported (maintainer): `0.1.0-run34`'s `EVAL-REPORT.md` shows Team Efficiency dropping to 1/5 and every
single sprint reporting `PR Merges (after): 0/0` - the team never shipped one release across all 5 sprints,
despite completing sprint reports every time (unlike `0.1.0-run33`, which at least merged 2 sprint releases).
The maintainer specifically asked whether a recent QA-gating change (`Gate create_sprint_report on QA
actually reaching Tested`, GH issue #246) was the cause.

Investigated directly against the real run (`gh run view 35979792714`, its `transcript.md` artifact, and
`eval/0.1.0-run34/*` in `horseless-carriage-eval-todo-app`). **The GH issue #246 QA gate is not the primary
cause** - `EVAL-REPORT.md`'s own top-problem #3 evidence ("Strict priority ordering in advance_story_stage
prevents advancing US-0003 until US-0002 is fully accepted") is ISSUE-0050's regression recurring, and #246
can only ever amplify an *existing* stall (it refuses to close a sprint report when QA reviewed a passing
build but no story reached `Tested` - which is also what happens when a story is *already* blocked upstream
by ISSUE-0050, not only when QA itself skips the hand-off it was designed to catch). The dominant,
independently-sufficient cause is unrelated to QA gating entirely:

**Root cause:** confirmed in `transcript.md` - `create_release_pr` was called 8 times across the run and
failed identically every time, starting with Sprint 1's very first attempt:
```
"message": "Failed to land the sprint report/transcript on develop before opening the release PR.",
"push": {
  "message": "git commit failed: ... Untracked files:\n\t.coverage\n\t__pycache__/\n\ttests/__pycache__/\n\n
              nothing added to commit but untracked files present (use \"git add\" to track)",
  ...
}
```
`create_release_pr`'s own flow (`agents/scrum_team/tools/github.py`) re-renders the sprint report/transcript
onto `develop`, stages only `specs/`/`.hc/` via `integrate_open_changes` (which - correctly - already commits
everything real by itself), then calls `_git_push_impl(..., add_all=False, ...)` to land the branch. By this
point there is usually nothing left for `_git_push_impl` to stage or commit (`integrate_open_changes` already
did), which is the expected, common case - `_git_push_impl` already had a fallback for exactly this
("nothing to commit" -> retry with `--allow-empty`). But `check_build()`/pytest runs leave `.coverage` and
`__pycache__/` sitting untracked in the working tree (this scenario repo has no `.gitignore` at all), and
`git` prints a **different** message when untracked files exist alongside nothing staged -
`"nothing added to commit but untracked files present"` - not `"nothing to commit, working tree clean"`. The
old code's fallback only string-matched the second phrasing (`"nothing to commit" in stderr+stdout`), missed
the first entirely, and hard-failed - every single time, in every sprint, since untracked pytest artifacts
are essentially guaranteed to be present by the time `create_release_pr` runs.

## Acceptance Criteria
- `_git_push_impl` decides whether there's anything to commit via a `git` plumbing check (exit-code based),
  not by string-matching one specific human-readable failure message - so it's correct regardless of which
  of git's several possible phrasings applies (also no longer dependent on git's output locale).
- The existing "commit failure for a real reason must stay fatal" behavior (GH issue #115) is unchanged -
  only the "was there really nothing to commit" detection method changed.

## Notes
- Not proposing a `.gitignore` for the eval scenario/seeded repos as part of this issue - the plumbing-based
  fix in `_git_push_impl` is the general, root-cause fix (any untracked file for any reason would have hit
  this, not just these two specific pytest artifacts), and benefits every caller of `_git_push_impl`
  identically (`git_push`, `create_release_pr`, `_sync_and_commit_roadmap_on_exhaustion`, `seed_repository`),
  in real usage too, not just this eval scenario.
- Every one of the 8 failed `create_release_pr` attempts this run also called `_checkout_develop_or_recover`
  first (its own docstring: used by `create_story_spec_pr`/`create_sprint_backlog_pr`/`start_feature_branch`/
  `create_release_pr`) - each failed attempt re-triggered ISSUE-0050's discard mechanism again. Fixing this
  issue means `create_release_pr` should now actually succeed, which removes the *repeated retries* that were
  amplifying ISSUE-0050's exposure in this run - but ISSUE-0050's own fix is what actually prevents the data
  loss regardless of retry count.

## Test Approach
- `agents/scrum_team/tests/test_github.py::TestGitHubTools::test_git_push_retries_as_empty_commit_when_nothing_staged_despite_untracked_files_present` -
  reproduces the exact real failure text (`"nothing added to commit but untracked files present"`), confirms
  the plumbing check catches it and retries with `--allow-empty` without ever attempting (or needing to
  string-match the failure of) a plain `git commit` first.
- The two pre-existing "nothing to commit" tests (`test_git_push_retries_as_empty_commit_when_nothing_to_commit`,
  `test_git_push_reports_error_when_empty_commit_retry_also_fails`) and the real-failure test
  (`test_git_push_reports_error_when_commit_fails_for_a_real_reason`) still pass unchanged in behavior.
- Full `agents/scrum_team/tests` suite: 643 passed (see ISSUE-0050's Test Approach for the exact invocation
  and the one pre-existing, environment-only integration-test failure with `--no-deps`).

## Resolution
- `agents/scrum_team/tools/github.py`: `_git_push_impl` now checks `git diff --cached --quiet`'s exit code
  before attempting a commit - `returncode == 0` means nothing is staged, so it goes straight to
  `git commit --allow-empty` instead of trying a plain commit first and string-matching its failure text
  afterward.
- `agents/scrum_team/tests/test_github.py`: new regression test reproducing the real untracked-files message;
  one pre-existing test's mock `fake_run` extended to answer the new `git diff --cached` call.
