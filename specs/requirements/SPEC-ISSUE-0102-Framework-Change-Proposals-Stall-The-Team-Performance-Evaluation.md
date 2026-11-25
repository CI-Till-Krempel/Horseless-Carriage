# Issue

- Issue ID: SPEC-ISSUE-0102
- Title: Framework change proposals stall the Team Performance Evaluation - they need a human the eval doesn't have
- Status: Done
- Priority: Should
- Owner: Horseless Carriage maintainers
- Last Updated: 2026-11-25

## Overview

GH issue #567 is title-only: "Framework change proposals should not stop the team performance
evaluation". A "framework change proposal" is the Architect's `propose_framework_change`
(`agents/scrum_team/tools/workflow.py`). It opens a draft PR that changes the target project's
`.hc/workbench-setup.sh` (and optionally `.hc/workbench-mcp.json`), and asks a human operator to
widen Horseless Carriage's own network allowlist. The Team Performance Evaluation (`run_eval.py`,
`INTERACTION_LEVEL=EVAL`) has no human. So every proposal the team made there became a dead end
that nobody could resolve, and the harness's stop-early checks treat that kind of dead end as a
reason to end the run.

There is no single hard gate. Three things combine (line numbers are `origin/main` at 85d12b4):

1. **An "ACTION NEEDED" item that can never be resolved.** `workflow.py:557-568` always calls
   `record_blocking_interaction("framework_change", "Workbench change needs new network access: ...")`
   when the proposal names allowlist hosts. Its detail says *"A human operator must add the listed
   host(s) to NET_PROXY_EXTRA_ALLOWLIST_HOSTS ... before this workbench's tests can actually reach
   them"*. The docstring (`workflow.py:396`) adds *"the Tested gate stays blocked until that
   operator-side step actually happens"*. In the eval, nobody can do that step. The eval's allowlist
   is the fixed, checked-in `config/net-proxy/allowlist-eval.filter` (via `docker-compose.eval.yml`),
   and it already covers `pypi.org`/`files.pythonhosted.org`/`registry.npmjs.org`. The team
   therefore spends turns on an interaction it can't close. In eval run **0.2.1-run95** (GH Actions
   run 45374553012), sprint 2 and sprint 3 both started with ScrumMaster calling
   `list_blocking_interactions` and reasoning about *"a framework/network issue"* /
   *"a framework change that's already in draft"*.
2. **The proposal PR is never merged, so the same proposal comes back every sprint.**
   `run_eval.py:338-381` `_merge_open_steering_prs` only sweeps `/steering/` head branches
   (`run_eval.py:369`). A `framework/workbench-...` PR stays open, so `.hc/workbench-setup.sh` never
   reaches develop. `check_workbench_tooling` (`workbench_templates.py:350`/`385`, also returned as
   `start_sprint`'s `workbench_check`) then reports `action_needed: True` again every sprint.
   In eval run **0.2.1-run94** (GH Actions run 43428752101), the Architect called
   `propose_framework_change(template_name="python-flask", ...)` in sprints 1, 2 and 3. That
   produced eval-repo draft PRs #887, #893 and #897 and three fresh
   `[ACTION NEEDED - framework_change]` lines, all for the same workbench.
3. **Anything a human-only blocker points at stops the whole run.** If the team escalates the
   proposal as a human-category finding (`add_impediment`/`add_retro_action(category="human",
   priority="high")`, e.g. "waiting for an operator to allowlist pypi.org"), then
   `_unresolved_high_priority_general_blocker` (`run_eval.py:570-586`) stops the run with
   `stop_reason = "human_blocker_unresolved"` (`run_eval.py:1216`). A "product"-category story
   blocker with the same content stops it immediately as `blocked_needs_human`
   (`run_eval.py:637`). That is the right reaction to a genuine human-only question. It is the
   wrong one for "the framework PR is waiting for review", because the harness itself is the
   stand-in reviewer.

A latent bug in the same sweep made point 2 worse. Both proposal tools open their PRs with
`draft=True` (`workflow.py:247`, `workflow.py:541`), and GitHub refuses to merge drafts. Every
steering auto-merge in run 0.2.1-run94's manifest failed with
`'GraphQL: Pull Request is still a draft (mergePullRequest)'` (PRs #890, #895, #900, #903, #905,
retried each sprint). So GH #405's steering sweep had not merged anything since it was added.

## Acceptance Criteria

- At `INTERACTION_LEVEL=EVAL`, `propose_framework_change` opens the same draft PR (with the
  PR-body "HUMAN OPERATOR ACTION REQUIRED" section unchanged) and records the same
  `framework_change_proposals` entry. It does **not** raise a `framework_change` blocking
  interaction, and its result carries an `eval_note` telling the team to continue the sprint.
- At `Product` and `CEO`, behavior is unchanged: the blocking interaction is still raised. Unknown
  levels fail closed (escalate).
- The eval harness merges open `framework/...` proposal PRs against the run's develop at the end
  of each sprint, alongside `steering/...` ones, never feature PRs. Each draft is marked ready
  (`gh pr ready`) before `gh pr merge`.
- A high-priority human-only general blocker, or an immediate `needs_human` story blocker, that is
  only a follow-up on a framework change proposal no longer stops the run. It matches if it
  references the proposal's PR URL or branch, or mentions workbench/allowlist/framework change.
  Any other human-only blocker still stops the run exactly as before. A framework-related story
  blocker still stops the run as `unresolved_across_sprint` if it survives a whole further sprint.
- Proposals stay visible: `_run_one_sprint` captures `framework_change_proposals` into the
  manifest, and `report.md` gets a "Framework Change Proposals" section (PR, harness merge outcome,
  template, requested hosts, rationale).
- Tests that fail on `origin/main` and pass with the fix:
  `test_framework_change.py::test_eval_level_records_the_proposal_but_raises_no_blocking_interaction`,
  `test_run_eval.py::...::test_skips_a_high_priority_blocker_that_only_waits_on_the_allowlist_step`,
  `test_run_eval_steering_merge.py::test_proposal_sweep_marks_a_draft_ready_before_merging_it`,
  `test_run_eval_analysis.py::test_framework_change_proposals_stay_visible_in_the_report`, and
  others in the same files.

## Notes

- GH issue #567. Related: GH #405 (the steering-PR auto-merge this extends, and whose draft-merge
  failure this also fixes), GH #342 (the human-category general-blocker stop), GH #336/#414 (the
  `needs_human` story-blocker stop), GH-456 (`check_workbench_tooling`), SPEC-ISSUE-0064 (the
  origin of `propose_framework_change`), ADR-0002 (allowlist proposals).
- **Guardrail note (AGENTS.md §12).** The `framework_change` blocking interaction is human-visibility
  plumbing for an allowlist request. It is not one of the mechanical gates listed in
  `docs/DEVELOPMENT-WORKFLOW.md` "Customization points", and no allowlist is changed by any of
  this. The tool still cannot touch `config/net-proxy/`, and the PR body still carries the operator
  section. It is, however, an "allowlist entries always go through human approval" path
  (`Architect-workflow.md`). The relaxation is therefore scoped to `INTERACTION_LEVEL=EVAL` only,
  following the existing `_PRE_*_BY_LEVEL` pattern in `helpers.py`, and the PR asks for explicit
  maintainer sign-off.
- Interpretation of the terse issue title: "stop" covers both the literal run stop
  (`human_blocker_unresolved`/`blocked_needs_human`) and the softer stall (re-proposing every
  sprint, turns spent on an unresolvable "ACTION NEEDED"). Neither observed CI run was literally
  ended by a framework proposal: run94 completed all 5 sprints, and run95 ended on a budget halt
  (see SPEC-ISSUE-0094). The fix closes both paths.
- The harness's merge does **not** rebuild the running agent image or change the run's network
  allowlist. Both are fixed for the run. A proposal that needs a host outside
  `allowlist-eval.filter` (e.g. Maven Central) still can't reach it mid-run. That shows up as real
  build/test failures for the team to deal with, not as a harness stop.

## Test Approach

- `agents/scrum_team/tests/test_framework_change.py`: EVAL level (no blocking interaction,
  `eval_note`, PR still draft with the operator section, proposal recorded with
  `operator_escalated: False`); Product and CEO levels still escalate.
- `agents/scrum_team/tests/test_run_eval_steering_merge.py`: the steering-only wrapper still ignores
  framework PRs; `_merge_open_proposal_prs` merges framework and steering PRs (with and without the
  eval branch prefix) and never feature PRs; each draft is marked ready before merging.
- `agents/scrum_team/tests/test_run_eval.py`: framework-related human-only general blockers (by
  marker or by the proposal's PR URL) are skipped; an unrelated high-priority blocker next to one
  still stops the run; a framework-related `needs_human` story blocker gets the one-sprint grace
  and still stops as `unresolved_across_sprint` afterwards.
- `agents/scrum_team/tests/test_run_eval_analysis.py`: the report renders the new section with the
  harness merge outcome, and omits it when there were no proposals.

## Resolution

Fixed for GH #567:
- `agents/scrum_team/helpers.py`: new `framework_change_escalates_to_operator(level)`
  (Product/CEO: True, EVAL: False, unknown: True).
- `agents/scrum_team/tools/workflow.py` `propose_framework_change`: escalation is gated on that
  helper; the proposal record gains `operator_escalated`; the result gains `eval_note` in EVAL.
- `agents/scrum_team/scripts/run_eval.py`: `_merge_open_proposal_prs` (steering and framework,
  `gh pr ready` before merge) replaces the steering-only sweep at the end-of-sprint call site, and
  `_merge_open_steering_prs` is kept as a wrapper. `_is_framework_change_followup` filters
  `_unresolved_high_priority_general_blocker` and the immediate `needs_human` path.
  `framework_change_proposals` is captured per sprint.
- `agents/scrum_team/scripts/run_eval_analysis.py`: new "Framework Change Proposals" report section.
