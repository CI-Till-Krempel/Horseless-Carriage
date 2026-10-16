# Issue

- Issue ID: SPEC-ISSUE-0054
- Title: `.hc/state.json` Merge Conflicts Dump The Entire Scrum State Into Tool Responses, Burning Sprint Budget And Stalling Velocity
- Status: Done
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
  raw file content - it is resolved mechanically, with zero conflict-marked content ever entering a
  tool response.
- `resolve_feature_branch_conflicts`'s response payload must not scale with sprint number / backlog
  size the way `.hc/state.json`'s raw content did before this fix - a `.hc/state.json`-only conflict
  now returns the same small, fixed-shape `{"status": "ok", "merged": true, ...}` response regardless
  of backlog length, never a `conflicted_files` entry for that path.
- A conflict on `.hc/state.json` alongside a genuine source-file conflict still surfaces only the
  source file to the agent.
- `resolve_feature_branch_conflicts` remains correct once its input changes: regenerating from stale or
  partial `tool_context.state` (e.g. a crash-recovered session) must not silently fabricate fields -
  covered by reusing the exact same `REPO_STATE_KEYS` snapshot `save_state_to_repo` itself uses, so
  behavior is identical to an ordinary (non-conflict) save.

## Notes
- Gap lived in `resolve_feature_branch_conflicts` (`agents/scrum_team/tools/github.py:1390-1516`)
  treating every conflicted file identically, including `.hc/state.json`.
- Deliberately **not** pursued, per the stated direction for the next release (moving the whole scrum
  state machine, including its state, into a separate MCP server with its own project-specific state
  repo - out of scope for this fix but the fix is shaped not to fight it):
  - A general JSON-level 3-way merge engine for `.hc/state.json` - unnecessary complexity for a file
    that's always a full regeneration of live state already (see below), and would be thrown away the
    moment state stops living in this repo's git history at all.
  - Excluding `.hc/state.json` from feature-branch commits entirely - a larger, riskier change (it's
    also the GH issue #59 crash-recovery checkpoint mechanism) for a problem the mechanical
    regenerate-and-stage fix below already fully neutralizes at the one place conflicts actually
    surface cost to an agent.
- **Key insight enabling the actual fix**: `.hc/state.json` is never hand-authored - `save_state_to_repo`
  (`agents/scrum_team/tools/scrum.py:482-501`) always fully regenerates it from live `tool_context.state`
  via `{k: tool_context.state.get(k) for k in REPO_STATE_KEYS}`. The in-memory session state is therefore
  always authoritative and current; a git conflict on the committed file is never a real content conflict
  to adjudicate, just two stale on-disk snapshots of a value that's already correct in memory. This also
  means the fix needs no new merge logic at all - just regenerate-and-stage using the exact same snapshot
  `save_state_to_repo` already builds.
- This is a different failure class from SPEC-ISSUE-0050/SPEC-ISSUE-0052 (no state was lost or regressed here -
  `stages_completed` only ever grew sprint over sprint in this run, confirmed via `manifest.json`); the
  cost here was pure token/turn waste on a file that was never meant to be a piece of reviewable
  application content in the first place.
- Also visible in the same stretch: DevTeam attempted `merge_story_pr` directly (event 47,
  `transcript.md:14173`) despite it not being in DevTeam's tool list, wasting one more turn before
  transferring to QA - minor compared to the state.json cost above and not fixed here.

## Test Approach
- `agents/scrum_team/tests/test_github.py::TestResolveFeatureBranchConflicts`: two new cases -
  `test_state_json_conflict_is_resolved_mechanically_not_surfaced` (a `.hc/state.json`-only conflict
  resolves to `status: ok, merged: true`, with the on-disk file regenerated from `tool_context.state`
  and matching `REPO_STATE_KEYS` exactly) and
  `test_state_json_conflict_excluded_even_when_a_real_conflict_also_exists` (a simultaneous `.hc/state.json`
  + `app.py` conflict surfaces only `app.py` in `conflicted_files`, and `.hc/state.json` is regenerated
  with no conflict markers left).
- Full `agents/scrum_team/tests` suite re-run (1009 passed, 1 skipped) to confirm no regression in the
  existing `TestResolveFeatureBranchConflicts` cases or anywhere else touching `.hc/state.json`.

## Resolution
- `agents/scrum_team/tools/github.py`: `resolve_feature_branch_conflicts` now special-cases
  `.hc/state.json` among the conflicted paths - instead of reading and surfacing its raw conflict-marked
  content, it regenerates the file from `tool_context.state` (reusing `scrum.py`'s own
  `REPO_STATE_KEYS`/`_write_state_atomically`) and stages it directly, exactly like an
  already-resolved-by-write_file file would be. No new merge logic, no change to what gets committed or
  where - a small, self-contained special case that is trivial to delete once `.hc/state.json` stops
  existing as a committed file in this repo at all.
- `agents/scrum_team/tests/test_github.py`: the two new `TestResolveFeatureBranchConflicts` cases above.
