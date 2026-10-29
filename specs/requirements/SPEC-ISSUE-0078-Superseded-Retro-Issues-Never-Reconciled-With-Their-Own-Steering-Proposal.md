# Issue

- Issue ID: SPEC-ISSUE-0078
- Title: A Retro-Filed Issue Superseded By A Steering Proposal Is Never Reconciled With It - Stays Orphaned, Still Blocking, Forever
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-10-29

## Overview
Reported by the maintainer, reviewing `0.1.0-run76` manually, as a follow-on to SPEC-ISSUE-0076/0077:
"aren't the spec issues really steering changes?" - prompted by seeing three open
"Steering proposal: QA-identity.md" PRs (`#626`, `#633`, `#636` in
`CI-Till-Krempel/horseless-carriage-eval-todo-app`) alongside SPEC-ISSUE-0001 through SPEC-ISSUE-0004
all still sitting `Ready`, never `Implemented`, in the same run.

**Confirmed mechanism.** This repo's own category-triage design (GH #342) already distinguishes
exactly this: `add_retro_action`/`add_impediment`'s `category` param routes a "technical" finding to
`_file_retro_items_as_issues` (auto-filed as a real `product_backlog` Issue, `tools/budget.py`), while a
"steering" finding is meant to route to `propose_steering_change` instead (a role-behavior/process
concern, not a code task). GH #381's own backstop (`create_sprint_report`'s
`unresolved_role_behavior_findings` check, `tools/budget.py:1087-1118`) exists specifically to catch the
common mis-triage case: "Ensure QA checks test suite run locally before marking Tested" was filed
`category="technical"` every time in `0.1.0-run76` (not "steering"), so `_file_retro_items_as_issues`
auto-filed it as a real Issue (SPEC-ISSUE-0001 through 0004, one per sprint) - and separately, once 2+
such role-behavior-shaped findings accumulated, the backstop correctly forced ScrumMaster to call
`propose_steering_change` for QA's identity, which it did, three times (confirmed: `#626`/`#633`/`#636`,
all targeting `QA-identity.md`).

Both halves worked exactly as designed. What's missing is the reconciliation between them:
`propose_steering_change` (`agents/scrum_team/tools/workflow.py`) had no way to know it was resolving
the SAME underlying concern an earlier `upsert_issue` call had already filed as a real backlog item -
it only ever recorded its own proposal (`steering_proposal_count`/`steering_proposals`), with nothing
touching `product_backlog` at all. So SPEC-ISSUE-0001 (and 0002, 0003, 0004) stayed exactly where they
were: `Ready`, Must-priority, permanently blocking the one-story-at-a-time ordering gate
(`_preceding_story`, GH #368) and permanently satisfying-but-never-clearing
`create_sprint_backlog_pr`'s `unaddressed_must_issues` gate - duplicated, orphaned busywork the team had
already done the right thing about through a completely different, correct mechanism.

## Acceptance Criteria
- `propose_steering_change` must accept a way to mark which existing `product_backlog` Issue(s) (if any)
  this specific proposal resolves, and must record that link on the matched item(s) on success only -
  never on a no-op (`new_content` identical to what's already there) or a failed proposal (PR creation
  errors), mirroring `steering_proposal_count`'s own "only a real success counts" rule.
- A superseded Issue must no longer block the one-story-at-a-time ordering gate (`_preceding_story`) -
  same treatment as a `blocked` item, since its real resolution path is now outside this pipeline
  entirely.
- A superseded Issue must no longer count toward `create_sprint_backlog_pr`'s `unaddressed_must_issues`
  gate - a sprint publish should not stay permanently gated on something already resolved a different
  way.
- Must not retroactively change the Issue's own `stages_completed` (it never reached `Accepted` through
  real implementation, and claiming it did would be dishonest) - only its *gating* behavior changes.
- A new regression test replaying this run's shape: a Must-priority Issue sitting `Ready`, then a
  `propose_steering_change` call naming it in `supersedes_issue_ids` - assert both gates (`ordering`,
  `unaddressed_must_issues`) stand down for it afterward, while an unrelated item is untouched.

## Notes
- Distinct from SPEC-ISSUE-0076 (filed from the same investigation line): that issue fixed a retro-filed
  Issue's *initial backlog position* so the ordering gate could ever see it as blocking in the first
  place. This issue is about what happens *after* the team has already resolved the underlying concern
  via a different, correct mechanism - the two are sequential, not overlapping gaps.
- Deliberately scoped narrowly: this does not attempt automatic matching (e.g. text-similarity between
  a steering proposal's rationale and an open Issue's title) - `supersedes_issue_ids` is an explicit,
  caller-provided list, the same "honest, not inferred" shape `implemented_via_earlier_work`
  (SPEC-ISSUE-0046) and `content_loss_acknowledgment` (SPEC-ISSUE-0075) already use for this class of
  escape hatch. ScrumMaster is already the role that reads both the original Issue (via
  `unresolved_role_behavior_findings`'s own quoted examples) and authors the steering proposal, so it
  has what it needs to name the connection explicitly.
- `superseded_by_steering` deliberately does not attempt to retroactively de-duplicate PAST
  near-identical Issues already filed before this fix existed (SPEC-ISSUE-0001 through 0004 in
  `0.1.0-run76` would still all need individually naming) - only prevents the *next* occurrence of this
  exact pattern from reaching the same dead end.

## Test Approach
- `agents/scrum_team/tests/test_steering_proposals.py`: `supersedes_issue_ids` marks the matching
  Issue(s) and returns their IDs; ignores an unknown ID; does not mark anything on a no-op proposal;
  does not mark anything if PR creation fails; omitting the parameter entirely leaves the backlog
  untouched (backward compatibility for every existing caller).
- `agents/scrum_team/tests/test_github.py`: `TestStartFeatureBranchOrderingGate` - a superseded Issue no
  longer blocks a lower-priority story via `start_feature_branch`; `TestCreateSprintBacklogPr` - a
  superseded Issue no longer counts toward `unaddressed_must_issues`.

## Resolution
Added an optional `supersedes_issue_ids: List[str] = None` parameter to `propose_steering_change`
(`agents/scrum_team/tools/workflow.py`). On a successful, real proposal (PR actually created, not a
no-op), each matching `product_backlog` item (`type == "Issue"` and `id` in the list) is marked with a
`superseded_by_steering` record (`pr_url`, `branch`, `role`) - never on a no-op or failed call. The
return value now includes `superseded_issue_ids` (the subset actually matched) for caller/test
visibility.

`_preceding_story` (`agents/scrum_team/tools/requirements.py`) now also skips a `superseded_by_steering`
item when scanning backward for the nearest non-blocked predecessor, exactly like it already skips a
`blocked` one. `create_sprint_backlog_pr`'s `unaddressed_must_issues` filter
(`agents/scrum_team/tools/github.py`) now also excludes `superseded_by_steering` items, alongside the
existing `blocked` exclusion.

Tests added:
- `agents/scrum_team/tests/test_steering_proposals.py`:
  `test_supersedes_issue_ids_marks_the_matching_issue`, `test_supersedes_issue_ids_ignores_an_unknown_id`,
  `test_supersedes_issue_ids_does_not_mark_anything_on_a_no_op_proposal`,
  `test_supersedes_issue_ids_does_not_mark_anything_if_the_pr_fails`,
  `test_omitting_supersedes_issue_ids_leaves_the_backlog_untouched`.
- `agents/scrum_team/tests/test_github.py`: `test_skips_an_issue_superseded_by_a_steering_proposal`
  (`TestStartFeatureBranchOrderingGate`), `test_does_not_block_on_an_issue_superseded_by_a_steering_proposal`
  (`TestCreateSprintBacklogPr`).

Full suite (`agents/scrum_team/tests`, via `docker compose run`, project-scoped per
`lib_docker.compose_project_args("test")`) passes 1113/1117 - the same pre-existing failures already
tracked (fixed by SPEC-ISSUE-0074's companion conftest.py change, landing in a separate PR) present and
unrelated to this change.
