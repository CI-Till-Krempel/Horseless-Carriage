# User Story

- Story ID: US-0030
- Title: Check Release Approval After the Release PR Opens, Not Before
- Status: Done
- Priority: Must
- Owner: Product Owner
- Last Updated: 2026-11-12

## As a Product-level human reviewer, I want the release PR to open before I'm asked to approve it, so I actually have something concrete to review instead of approving an increment sight-unseen.

## Acceptance Criteria
- Given the "Product" interaction level (where `record_human_approval("release", ...)` is required) and no fresh release approval recorded yet, when `create_release_pr(...)` is called, then the develop -> main PR is still opened (`gh_pr_create` is actually invoked) rather than refused outright.
- Given the PR has just opened and approval is still missing, then `create_release_pr` returns `{"status": "ok", "released": False, ...}` (not a hard "error" — PR creation itself succeeded) and records a blocking interaction (`record_blocking_interaction`) that names the real, just-opened PR, not a vague "no PR yet" message.
- Given a human then reviews that PR and calls `record_human_approval("release", ...)`, when `create_release_pr(...)` is called again, then it does not try to re-open a second PR (idempotent re-call via the same existing-open-PR lookup `release_pr_still_open` already uses) and instead finishes the release (`"released": True`), bumping `release_approval_baseline` and clearing `sprint_report_pending_release`/`sprint_backlog_pr_sprint`.
- Given approval is still outstanding, then `sprint_report_pending_release`/`sprint_backlog_pr_sprint` do NOT clear yet — preserving the existing invariant (SPEC-ISSUE-0066) that "sprint cleanly finished" implies the release was actually approved, not merely that a PR happens to be open.
- Given the "CEO"/"EVAL" interaction levels (no release approval required), then `create_release_pr` behaves exactly as before: the PR opens and the release is considered done immediately.

## Notes
- Reported as GH-452 ("Approval of the sprint should be done after creating the sprint PR"): the issue's title says "sprint PR" and its body says "release PR" — RELEASE.md already documents `create_release_pr` as "the `develop` -> `main` 'sprint PR'", so both phrasings point at the same function.
- `create_sprint_backlog_pr` (the actual Sprint Backlog PR, develop-targeted) already had this shape going in — it opens/pushes its own PR unconditionally and only gates the *merge* on approval afterward. This story brings `create_release_pr` in line with that existing, already-correct pattern rather than inventing a new approval UX.
- Design/technical notes: the approval gate (`required_pre_release_approval`/`record_blocking_interaction`) moved from the top of `create_release_pr` (agents/scrum_team/tools/github.py) to right after the PR-open/idempotent-recall step. The "release is done" bookkeeping (`release_approval_baseline`, `sprint_report_pending_release`, `sprint_backlog_pr_sprint`) only updates once approval is satisfied (or isn't required), not merely once the PR opens - this is what keeps the existing "team can't barrel ahead" guarantee intact, just re-anchored to after PR creation instead of before it.

## Test Approach
- `agents/scrum_team/tests/test_github.py::TestGitHubTools::test_create_release_pr_opens_the_pr_even_without_a_fresh_release_approval` — PR creation isn't blocked by a missing approval.
- `...::test_create_release_pr_rejection_records_blocking_interaction` — the blocking interaction references the real PR number.
- `...::test_create_release_pr_without_approval_does_not_mark_the_sprint_closed` — `sprint_report_pending_release`/`sprint_backlog_pr_sprint` stay untouched while approval is outstanding.
- `...::test_create_release_pr_recall_after_approval_does_not_reopen_the_pr` — idempotent re-call after approval finishes the release without a duplicate `gh pr create`.
- Full existing `create_release_pr` suite (opens-develop-to-main, resets-sprint-backlog-pr-sprint, surfaces-pr-create-failure, lands-report, changelog tests, CEO/EVAL levels) re-run and passing unchanged.
