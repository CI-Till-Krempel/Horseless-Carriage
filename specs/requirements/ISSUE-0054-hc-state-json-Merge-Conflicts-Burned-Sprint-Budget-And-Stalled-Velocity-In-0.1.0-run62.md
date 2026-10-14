# Issue

- Issue ID: ISSUE-0054
- Title: `.hc/state.json` Merge Conflicts Dump The Entire Scrum State Into Tool Responses, Burning Sprint Budget And Stalling Velocity
- Status: Ready
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-14

## Overview
Reported (`0.1.0-run62`'s `report.md`): all 5 sprints completed, but every single sprint exceeded the
5,000,000-token budget (Sprint 1: 5.30M, Sprint 2: 5.99M, Sprint 3: 6.07M, Sprint 4: 6.46M, Sprint 5:
7.03M - a steady upward climb despite the team doing *less* work each sprint), and velocity collapsed
to 0 stories implemented in both Sprint 4 and Sprint 5. `manifest.json` confirms US-0006 ("View All
Lists and Tasks") never advanced past `Draft`/`Ready` across all 5 sprints even though Sprint 4's and
Sprint 5's sprint goals both explicitly named it, and US-0005 reached `Tested` in Sprint 4 but made
*zero* further progress in Sprint 5 despite that sprint alone burning 7.03M tokens.

**Confirmed mechanism.** `.hc/state.json` - the harness's own bookkeeping file holding the full
`product_backlog`, `retro_actions`, `decision_log`, `blocking_interactions`, etc. - is committed to
every feature branch via `save_state_to_repo`/`_checkpoint_state_commit`
(`agents/scrum_team/tools/scrum.py:435`, `agents/scrum_team/tools/github.py:68-77`). Because virtually
every tool call mutates this single shared file, and every story's feature branch and `develop` both
carry their own independent commits touching it, a feature branch merging `develop` back in hits a
real git conflict on `.hc/state.json` essentially every time the backlog has grown since that branch
was cut.

`resolve_feature_branch_conflicts` (`agents/scrum_team/tools/github.py:1390`) handles this the same
way it handles any conflicted file: it returns the **full on-disk content, conflict markers
included**, for DevTeam to hand-resolve via `write_file`:

```python
if any(marker in content for marker in _CONFLICT_MARKERS):
    unresolved[rel_path] = content
```

For an application source file this is reasonable - a human-sized diff. For `.hc/state.json` it is
not: by Sprint 5 the file is tens of KB of backlog/retro/decision-log JSON. The transcript shows this
exact round-trip twice, once per late sprint:
- Sprint 4, event ~56 (`transcript.md:12821`): a 70KB `resolve_feature_branch_conflicts` response
  embeds the full conflicted `.hc/state.json`.
- Sprint 5, event 56 (`transcript.md:14283`): a 26KB conflict response, followed two events later
  (event 57, `transcript.md:14291`) by DevTeam's `write_file` call reproducing the **entire file
  again** (another ~26KB) just to hand-resolve the merge - and the very next
  `resolve_feature_branch_conflicts` retry (event 59-60) fails outright (*"you need to resolve your
  current index first"*), requiring a further `integrate_open_changes` recovery call (event 61-62)
  before the branch is even back to a clean state. One merge conflict on a file DevTeam has no
  business editing costs on the order of 15-20k tokens and 5+ wasted tool-call turns, purely on
  internal bookkeeping, before any real application work resumes.

This directly explains both observed symptoms:
- **The budget climb**: `.hc/state.json` only grows sprint over sprint (more stories, more retro
  actions, more decision-log entries, more blocking interactions) - so the cost of every conflict
  round-trip on it grows too, independent of how much real work is happening. Sprint 5's conflict
  payload is smaller than Sprint 4's only because by then more of the backlog had collapsed into
  `Accepted` entries with less diff-relevant detail; the underlying pattern is the same.
- **Zero velocity in Sprint 5**: DevTeam's entire Sprint 5 session (91 events total, versus Sprint 1's
  300+ for comparable token spend) was dominated by re-litigating US-0005's already-`Tested` status and
  recovering from this exact conflict/checkout failure sequence, leaving no budget or turns left to
  even open a feature branch for US-0006 before the 5M (then 7M) ceiling hit.

## Acceptance Criteria
- A merge conflict on `.hc/state.json` specifically must never require an LLM agent to hand-merge the
  raw file content. Either:
  - `.hc/state.json` conflicts are resolved mechanically before `resolve_feature_branch_conflicts`
    ever surfaces them to an agent (e.g. a structural/field-level merge of the two JSON versions, or
    regenerating the feature branch's `state.json` from current in-memory state and re-committing),
    or
  - `.hc/state.json` is excluded from feature-branch commits entirely (e.g. committed only on
    `develop`/release branches, never diffed/merged as part of a story's own feature-branch PR) so a
    story branch simply never carries a conflicting copy of it.
- `resolve_feature_branch_conflicts`'s response payload for any file must not scale with the sprint
  number / total backlog size the way `.hc/state.json`'s does today - a regression test asserts its
  response size for a `.hc/state.json`-only conflict stays bounded (e.g. a structured diff/summary, not
  the full file) regardless of backlog length.
- A new test reproduces the Sprint 5 double-failure shape (conflict on `.hc/state.json` -> resolved via
  write_file -> immediate "resolve your current index first" on retry) and asserts the fixed path
  either doesn't hit the conflict at all, or recovers in a single call without requiring
  `integrate_open_changes` as a follow-up rescue.

## Notes
- Gap lives in `resolve_feature_branch_conflicts` (`agents/scrum_team/tools/github.py:1390-1516`) and
  the unconditional `.hc/state.json` commit-to-feature-branch behavior in `save_state_to_repo`/
  `_checkpoint_state_commit` (`agents/scrum_team/tools/scrum.py`) and `_commit_state_and_specs`
  (`agents/scrum_team/tools/github.py:68-77`).
- This is a different failure class from ISSUE-0050/ISSUE-0052 (no state was lost or regressed here -
  `stages_completed` only ever grew sprint over sprint in this run, confirmed via `manifest.json`); the
  cost here is pure token/turn waste on a file that was never meant to be a piece of reviewable
  application content in the first place.
- Also visible in the same stretch: DevTeam attempted `merge_story_pr` directly (event 47,
  `transcript.md:14173`) despite it not being in DevTeam's tool list, wasting one more turn before
  transferring to QA - minor compared to the state.json cost above and not filed separately, but worth
  a prompt nudge if it recurs.

## Test Approach
- `agents/scrum_team/tests/test_github.py`: new cases around `resolve_feature_branch_conflicts`
  specifically for `.hc/state.json` conflicts - assert the response no longer embeds raw conflicted
  content for this path, and that resolution succeeds without a separate `integrate_open_changes` call.
- `agents/scrum_team/tests/test_story_pipeline_state_machine.py` or similar: a backlog-size-scaling
  test confirming a conflict-response payload size bound that holds regardless of `product_backlog`
  length.
- Re-run (or replay) `0.1.0-run62`'s Sprint 4/5 transcript shape against the fixed tools and confirm
  token cost for the conflict-resolution sequence drops substantially, with headroom left in the sprint
  budget for US-0006 to actually start.
