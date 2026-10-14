# Issue

- Issue ID: ISSUE-0058
- Title: A Failed/Incomplete Release PR At Sprint Close Has No Recovery Path - `start_sprint` Can Never Succeed Again For The Rest Of That Sprint
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-14

## Overview
Reported (`0.1.0-run65`'s `report.md`): the run stopped after 2 of 5 sprints with `stop_reason:
budget_critical_halt`. Sprint 2 alone burned 6,253,807 tokens (125% of its 5,000,000 budget) and
accepted **zero** of the 5 remaining stories - Say-Do Ratio stayed pinned at 0.17 (1/6) across both
sprints, unchanged. Unlike `0.1.0-run64` (ISSUE-0056), this isn't a case of the team never calling
`start_sprint` again - the transcript shows `start_sprint` called twice in Sprint 2
(`transcript.md:4644`, `4772`). Both failed, and the reason they failed means neither could ever have
succeeded, structurally, for the rest of that sprint.

**Confirmed mechanism.** Sprint 1 ends (`transcript.md:4561-4568`, `Stop reason: sprint_report_produced
- 278 events`) having accepted US-0001 and produced a sprint report, but its own, sole attempt to open
a release PR (event 274, `transcript.md:4472-4479`) failed:

```
"stderr": "pull request create failed: GraphQL: No commits between eval/0.1.0-run65/main and
eval/0.1.0-run65/develop (createPullRequest)"
```

Sprint 1 ends anyway (the host-side "always produce a report" backstop, not a release gate), leaving
`sprint_report_pending_release` stuck `True`. Sprint 2 begins; ScrumMaster correctly calls
`start_sprint` (event 5, `transcript.md:4644`) - and `new_sprint_item_blocked` correctly refuses it:

```
"Cannot plan new sprint work - the previous sprint's retrospective/report was completed but
create_release_pr was never called (or didn't succeed) for it, and it still has stories short of
Accepted (['US-0002', 'US-0003', 'US-0004', 'US-0005', 'US-0006']). Finish the previous sprint's
release (create_release_pr) before starting new work..."
```

ProductOwner does exactly what the message asks - `record_human_approval` then `create_release_pr`
(events 9-12, `transcript.md:4677-4753`), which succeeds this time, opening PR #541. ScrumMaster
retries `start_sprint` immediately (event 15, `transcript.md:4772`) - **still refused**:

```
"Cannot start a new sprint - the previous sprint's release PR (develop -> main) is still open and
hasn't been merged yet. create_release_pr only ever opens that PR, never merges it - merging is
always a separate, out-of-band step..."
```

That "separate, out-of-band step" is `run_eval.py`'s `_merge_open_prs` (`agents/scrum_team/scripts/
run_eval.py:261-295`) - and it runs exactly **once per sprint**, immediately after that sprint's
`_run_one_sprint` call returns (`run_eval.py:885`, right inside the sprint loop in `_main_async`).
Nothing re-invokes it mid-sprint. Since Sprint 1's own sweep found no PR to merge (the release PR
failed to even open then), and PR #541 doesn't exist until partway through Sprint 2, the *earliest*
the harness can attempt to merge it is at the Sprint 2 -> 3 boundary - which never arrives, because
`start_sprint` keeps refusing in the meantime. The team has no tool of its own that can merge a
release PR (`merge_story_pr` is explicitly scoped to story feature-branch PRs only, per
`run_eval.py:881-884`'s own comment). This is a closed loop: `start_sprint` needs the release PR
merged; only the harness can merge it; the harness only tries between sprints; no further sprint
boundary occurs until `start_sprint` succeeds.

When the harness finally does sweep PR #541 (after Sprint 2 itself ends), it's now **genuinely
unmergeable** - `develop` moved further in the meantime (US-0002 planning, `ISSUE-0002` filing, etc.):

```json
{"number": 541, "merged": false, "message": "X Pull request .../541 is not mergeable: the merge
commit cannot be cleanly created.\nTo have the pull request merged after all the requirements have
been met, add the `--auto` flag.\nRun the following to resolve the merge conflicts locally: ..."}
```

`_merge_open_prs` does a single flat `gh pr merge --merge --admin` with no retry and no conflict
handling at all - unlike `resolve_feature_branch_conflicts` for story feature branches, there is no
equivalent tool or path for resolving a conflicted release PR. The run had already spent its entire
Sprint 2 budget (6.25M tokens, 2 `start_sprint` attempts, one correct recovery attempt) chasing a gate
that could never open for it.

## Acceptance Criteria
- A `create_release_pr` call that succeeds *mid-sprint* (as the correct recovery attempt here did)
  must have a real chance to actually unblock `start_sprint` within that same sprint - e.g. the
  harness's merge sweep is also triggered right after a successful `create_release_pr` call, not only
  at the end of `_run_one_sprint`, or `start_sprint`'s own gate can trigger/request a merge attempt
  itself rather than only ever checking passively.
- A release PR that becomes genuinely conflicted before it can be merged (as PR #541 did here) has a
  real recovery path - either a conflict-resolution tool analogous to `resolve_feature_branch_conflicts`
  for the develop->main release PR specifically, or the harness's sweep itself attempts a mechanical
  resolution (e.g. re-opening the PR fresh against current `develop` after a failed merge) instead of
  logging `merged: false` and leaving it permanently stuck.
- Whichever shape is chosen, "the team spends an entire sprint's token budget in a state where
  `start_sprint` can structurally never succeed, with zero way to detect or escape that from within
  the session" must not be possible - at minimum, a bounded number of failed `start_sprint` retries for
  the same underlying reason should be mechanically detectable (the same spirit as the existing
  transfer-loop/repeated-call breakers), surfacing a real, actionable blocker instead of silent
  budget burn.
- A new regression test: simulate Sprint 1 ending with `sprint_report_pending_release=True` and no
  release PR open; Sprint 2 opens one successfully mid-sprint; simulate `develop` advancing further
  before any merge attempt occurs - assert the fix's recovery path (whichever shape is chosen) actually
  succeeds instead of leaving `start_sprint` permanently blocked for the rest of that sprint.

## Notes
- Distinct from ISSUE-0056: that issue is about `start_sprint` never being *called* again at all
  (`0.1.0-run64`). Here, it's called correctly and repeatedly, and the team even correctly diagnoses
  and attempts the fix the error message asks for (`create_release_pr`) - the gap is structural
  (a between-sprint-only merge sweep can't help a mid-sprint recovery attempt), not an agent behavior
  problem. Both land in the same visible symptom (budget burned, phase/sprint stuck), so whoever
  implements either fix should check the other issue's regression tests still pass.
- Gap lives in the interaction between `start_sprint`/`new_sprint_item_blocked`
  (`agents/scrum_team/tools/scrum.py`), `create_release_pr` (`agents/scrum_team/tools/github.py`), and
  `_merge_open_prs`'s once-per-sprint-boundary placement in `run_eval.py`'s sprint loop
  (`agents/scrum_team/scripts/run_eval.py:862-893`).
- **Resolved**: why Sprint 1's *first* `create_release_pr` attempt hit "No commits between
  eval/0.1.0-run65/main and eval/0.1.0-run65/develop" - US-0001 never actually merged into `develop`
  at all. `start_feature_branch` failed 4 times for DevTeam on US-0001 (unplanned into the sprint,
  then the sprint-backlog-engagement gate - `transcript.md:1978-1985`, `2654-2661`), so DevTeam gave up
  on it and fell back to its own `git_push` + `gh_pr_create` calls directly (`transcript.md:2796-2935`)
  - both without ever passing a `base` parameter. `gh_pr_create` silently defaults an unset `base` to
  `_default_push_branch(tool_context)` (`agents/scrum_team/tools/github.py:680`) - i.e. **main**, not
  develop - so PR #540 ("US-0001: Create To-Do List") opened and merged straight into `main`,
  completely bypassing `develop`. By the time `create_release_pr` ran, `develop` was still sitting at
  the original seed commit with nothing to release - hence "no commits between them". This is the true
  upstream root cause of this issue's entire chain, filed separately as ISSUE-0059 (the general,
  more severe gap: any role with direct `gh_pr_create` access can silently corrupt GitFlow by falling
  back to it after `start_feature_branch` repeatedly rejects a story, with no guardrail distinguishing
  "this PR is shaped like a story feature branch, it must target develop" from "this is some other kind
  of PR, main is a safe default"). Fixing ISSUE-0059 would likely prevent this specific run's trigger
  entirely - but the *this issue's* finding (no recovery path once a release PR fails for ANY reason)
  remains real and worth fixing independently, since other causes of a failed release PR will keep
  recurring (`0.1.0-run64` hit a different one - a stale git tag - and would have hit this same trap
  too, had its team remembered to retry `start_sprint`).
- `0.1.0-run64` (ISSUE-0056) also never got a release PR merged for its final sprint
  (`sprint_report_pending_release: true` there too) - worth checking whether that run would have hit
  this exact same unrecoverable-retry trap had the team there also remembered to call `start_sprint`
  again, i.e. these two issues may compound in most real runs rather than being mutually exclusive.

## Test Approach
- `agents/scrum_team/tests/test_run_eval.py::TestRunOneSprintMidSprintReleasePrMerge` - a
  `create_release_pr` success mid-sprint triggers an immediate merge attempt (given `local_path`/
  `base_branch`); no trigger without them (backward compatible); no trigger for an unrelated tool's
  success.
- `agents/scrum_team/tests/test_run_eval.py::TestUnresolvableReleasePrConflict` - distinguishes a
  genuine, unresolvable merge conflict from a "not yet ready" failure that might still resolve, and
  from a clean merge.
- Full `agents/scrum_team/tests` suite re-run (1019 passed, 1 skipped).

## Resolution
- `agents/scrum_team/scripts/run_eval.py`: new `_start_fresh_sprint_session`-sibling change -
  `_run_one_sprint` now accepts optional `local_path`/`base_branch`; when given, a `create_release_pr`
  tool response with `status: ok` immediately triggers `_merge_open_prs`, instead of waiting for the
  sprint to end. Results are returned as `mid_sprint_pr_merges` and folded into
  `manifest["pr_merges"]` by `_main_async`, tagged `mid_sprint: True`.
- New `_unresolvable_release_pr_conflict` helper distinguishes a genuine, unresolvable git conflict
  (`"not mergeable"` in the `gh pr merge` failure message - the literal wording `0.1.0-run65`'s PR #541
  hit) from a failure that might still resolve on its own. Applied at both the new mid-sprint trigger
  and the existing end-of-sprint sweep: either one hitting a genuine conflict now stops the run cleanly
  (`stop_reason: "release_pr_conflict_unrecoverable"`) instead of silently continuing to burn further
  sprints' budget on a `start_sprint` retry loop that can never succeed - no automatic git conflict
  resolution was implemented (too risky for a harness script to get right on real content conflicts),
  matching the issue's own acceptance criteria preference for "surfacing a real, actionable blocker"
  over silent budget burn.
- `agents/scrum_team/tests/test_run_eval.py`: the two new test classes above.
