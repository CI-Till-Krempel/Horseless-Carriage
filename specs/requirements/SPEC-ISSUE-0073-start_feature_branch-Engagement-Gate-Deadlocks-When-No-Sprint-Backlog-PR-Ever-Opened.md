# Issue

- Issue ID: SPEC-ISSUE-0073
- Title: `start_feature_branch`'s Team-Engagement Gate Deadlocks When `create_sprint_backlog_pr` Published Directly To `develop` (No PR Ever Opened)
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-27

## Overview
Reported while investigating `0.1.0-run75`: the maintainer saw `❌ [DevTeam] gh_pr_comments failed: no
pull requests found for branch "sprint-backlog/1"` and suspected it was the root cause of the whole
run's failure. Confirmed - it is.

**Confirmed mechanism.** Sprint 1's planning output was already committed straight to `develop` by
`start_sprint`'s own cleanup sweep before `create_sprint_backlog_pr` ever ran, so by the time it ran
there was nothing left to push to a `sprint-backlog/1` branch or open a PR for
(`transcript.md:1589-1595`). This is a recognized, intentional path
(`agents/scrum_team/tools/github.py:1090-1142`): it treats the sprint as "published" anyway so the team
isn't stuck, sets `sprint_backlog_pr_sprint = sprint_number`, and returns
`{"status": "ok", "merged": true, ...}` with a warning that review was skipped - **no PR is ever
created** in this path, by design.

The problem is `start_feature_branch`'s separate team-engagement gate
(`agents/scrum_team/tools/github.py:815-840`, added for GH issue #357) does not know about this path. It
unconditionally requires `Architect`, `DevTeam`, and `QA` to each have bumped `pr_review_calls` (via a
successful `gh_pr_comment`/`gh_pr_review`) above `sprint_backlog_engagement_baseline` before any
feature branch can start - i.e. it requires real PR comments on "this sprint's Sprint Backlog PR"
regardless of whether that PR exists. When it doesn't:
- DevTeam's `gh_pr_comments` call resolves by the locally checked-out `sprint-backlog/1` branch (no
  `pr_id` given) and fails: `"no pull requests found for branch \"sprint-backlog/1\""`
  (`transcript.md:1671-1685`, repeated identically at `transcript.md:1756-1773`, `1810-1831`).
- Falling back to `gh pr list` (`transcript.md:1844`) confirms it too: `"There is no pull request
  associated with [sprint-backlog/1]"`.
- Architect's and QA's own sign-off attempts hit the exact same resolution failure
  (`transcript.md:2365-2383`, `2444-2462`).
- `_record_pr_review_call` (`github.py:1836-1850`) only fires on `gh_pr_comment`/`gh_pr_review` success
  (`github.py:1919-1920`, `1936-1937`) - since every attempt errors before that point, `pr_calls` for
  all three roles never moves past the baseline, so the gate can **never** be satisfied through any
  legitimate tool call. This is a true deadlock, not a slow retry.
- `start_feature_branch` itself repeated the identical rejection on every retry
  (`transcript.md:2336`, `2736`), and `raise_story_blocker`/`resolve_story_blocker` don't help either -
  that mechanism only clears a story's own `blocked` flag, which is orthogonal to
  `sprint_backlog_engagement_baseline`/`pr_review_calls`. Architect's own resolution text
  (`transcript.md:2824-2829`) says as much: *"...DevTeam can proceed with implementation directly or
  via local branch checkout without calling `start_feature_branch`..."* - the team's only way out was
  to abandon the tool and commit/push/open the feature PR by hand
  (`transcript.md:3077-3154`, raw `write_file`/`git_push`/`gh_pr_create` calls instead of
  `start_feature_branch`).

**Cost.** This loop ran from roughly event 91 (`transcript.md:1580`, the `create_sprint_backlog_pr`
call that took the no-PR path) through event 198 (`transcript.md:2829`, Architect's workaround) - about
a third of the sprint's entire 192-event transcript - before the team gave up on the tool and
hand-rolled the feature branch. `report.md` records Sprint 1 burning 4,553,833 tokens (84% of the run's
budget) to deliver exactly one story, and the run stopped early after Sprint 1 with
`stop_reason: budget_critical_halt` (`manifest.json`) - only `US-0001` ever reached `Accepted`;
`US-0002`-`US-0006` sat at `Ready` for the rest of the (truncated) run. This single deadlock-and-
workaround loop is the dominant contributor to that token burn and, by extension, to the run stopping
after 1 of 5 requested sprints.

## Acceptance Criteria
- When `create_sprint_backlog_pr` takes its "content already landed directly on `develop`, no PR
  opened" success path (`github.py:1090-1142`), it must record that fact in state (e.g. a
  `sprint_backlog_pr_has_no_pr` / equivalent flag, keyed to `sprint_number`) rather than leaving no
  trace beyond the one-off warning string in its return value.
- `start_feature_branch`'s engagement gate (`github.py:815-840`) must check that flag before demanding
  `gh_pr_comment`/`gh_pr_review` activity: if this sprint's backlog was published with no PR ever
  opened, the gate must not require comments on a PR that cannot exist. Either skip the check entirely
  for that sprint (consistent with `create_sprint_backlog_pr`'s own "review was skipped, sprint isn't
  stuck" stance), or substitute a reachable alternative (e.g. accept sign-off recorded some other way)
  - but it must not be able to demand something structurally impossible to satisfy.
- A story must never be forced into "abandon `start_feature_branch` and hand-roll git/gh calls" as its
  only escape route, the way `0.1.0-run75` was.
- A new regression test replaying this run's exact shape: `create_sprint_backlog_pr` takes the no-PR
  success path for sprint N, then `start_feature_branch` is called for a story in that sprint -
  asserts it does **not** return the "Architect, DevTeam, QA have not yet left a gh_pr_comment/
  gh_pr_review" error.
- Does not regress the normal case: when `create_sprint_backlog_pr` does open and merge a real PR,
  `start_feature_branch` must still correctly require Architect/DevTeam/QA engagement on it before
  work starts (the GH issue #357 behavior this gate exists for).

## Notes
- Gap lives in two places that disagree with each other: `create_sprint_backlog_pr`'s no-PR success
  path (`agents/scrum_team/tools/github.py:1090-1142`, specifically the `content_already_landed` branch
  at `1114-1142`) versus `start_feature_branch`'s engagement gate
  (`agents/scrum_team/tools/github.py:815-840`). The former already decided "review was skipped, don't
  block the sprint on it"; the latter was never told, and had no way to find out short of the global
  `sprint_backlog_pr_sprint`/`sprint_backlog_engagement_baseline` fields it already reads
  (`github.py:823-824`), neither of which distinguishes "a real PR exists but nobody's commented yet"
  from "no PR was ever opened to comment on."
- `raise_story_blocker`/`resolve_story_blocker` are not a fix here and should not be used as one - they
  toggle a per-story `blocked` flag that `start_feature_branch`'s engagement gate doesn't consult at
  all (confirmed in `0.1.0-run75`: `resolve_story_blocker` reported `US-0001` "no longer BLOCKED"
  at `transcript.md:2832-2838`, then the very next real attempt to advance the story still had to route
  around `start_feature_branch` entirely rather than through it).
- Distinct from SPEC-ISSUE-0072 (`sprint_backlog` pruning) and SPEC-ISSUE-0070 (`sprint_number`/phase
  advance) - both already landed on this branch's history and are unrelated to this gate.

## Test Approach
- `agents/scrum_team/tests/test_github.py`: construct state where `create_sprint_backlog_pr` is called
  with planning output already committed to `develop` (mocking the git/gh boundary so
  `integrate_open_changes` finds nothing to stage, per the existing `content_already_landed` test
  pattern in this file) - assert whatever new flag/field is introduced gets set.
- Add a `TestStartFeatureBranch` (or extend the existing one) case: with that flag set and
  `pr_review_calls` left untouched (no role has commented on anything), call `start_feature_branch` for
  a Ready, planned story and assert it succeeds (or at least does not fail with the
  gh_pr_comment/gh_pr_review message) - contrasted with the existing/expected case where a real Sprint
  Backlog PR exists and engagement genuinely hasn't happened yet, which must still fail.
- Replay `0.1.0-run75`'s shape end-to-end if feasible: Sprint 1 planning lands directly on `develop`,
  `create_sprint_backlog_pr` succeeds via the no-PR path, then `start_feature_branch('US-0001', ...)`
  is called immediately after - assert no `gh_pr_comments`/`gh_pr_comment`/`gh_pr_review` calls are
  required to succeed.

## Resolution
Added a new sprint-scoped state field, `sprint_backlog_pr_opened` (`agents/scrum_team/tools/scrum.py`,
alongside `sprint_backlog_pr_sprint` in `REPO_STATE_KEYS` and the `setdefault` block, defaulting to
`True` so any state saved before this fix keeps requiring real engagement exactly as before). Both of
`create_sprint_backlog_pr`'s success paths (`agents/scrum_team/tools/github.py`) now set it alongside
`sprint_backlog_pr_sprint`: `False` on the "content already landed directly on develop, no PR opened"
shortcut, `True` on the normal push/open/merge path. `start_feature_branch`'s engagement gate
(`github.py:815-840`) now reads it (only when `sprint_backlog_pr_sprint` matches the *current*
`sprint_number` - otherwise treated as `True`/binding, same as before this fix) and skips the
Architect/DevTeam/QA `gh_pr_comment`/`gh_pr_review` requirement entirely when it's `False`, instead of
demanding comments on a PR that mechanically cannot exist.

Tests added (`agents/scrum_team/tests/test_github.py`):
- `TestStartFeatureBranchTeamEngagementGate::test_allows_starting_with_no_engagement_when_no_sprint_backlog_pr_was_ever_opened` -
  the direct regression test replaying `0.1.0-run75`'s shape.
- `TestStartFeatureBranchTeamEngagementGate::test_still_refuses_when_this_sprints_pr_was_opened_but_engagement_baseline_cleared_a_prior_sprints_no_pr_flag` -
  confirms the gate still binds normally once a real PR is open.
- `TestCreateSprintBacklogPr::test_recognizes_content_already_landed_via_a_different_path_as_published` -
  extended to assert `sprint_backlog_pr_opened` is `False` after the no-PR path.
- `TestCreateSprintBacklogPr::test_snapshots_engagement_baseline_on_the_first_merge_this_sprint` -
  extended to assert `sprint_backlog_pr_opened` is `True` after a real merge.

Full suite (`agents/scrum_team/tests`, via `docker compose run`, project-scoped per
`lib_docker.compose_project_args("test")`) passes: 1102 of 1107 tests, the same 5 pre-existing failures
(`test_agent.py`'s identity-fallback test and 4 in `test_steering_proposals.py`) present and unrelated,
same baseline SPEC-ISSUE-0072 already confirmed via A/B comparison.
