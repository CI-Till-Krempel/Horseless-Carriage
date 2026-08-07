# Issue

- Issue ID: ISSUE-0048
- Title: Roadmap Version Section Garbled By A Combined ID+Title Story Reference (Not A Develop/Main Branch-Sync Gap)
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-08-07

## Overview
Reported (maintainer, reviewing `0.1.0-run28`): the roadmap out-of-sync problem looked like the last
changes to `specs/ROADMAP.md` were only committed to `develop`, never merged to `main` before the sprint
report/eval report got created - asked to investigate and, if confirmed, update the sprint wrap-up logic
to merge `develop` into `main` first.

Investigated directly against the run's eval-repo branches: `eval/0.1.0-run28/develop` and
`eval/0.1.0-run28/main`.

**The develop/main hypothesis does not hold for this run - verified directly, not assumed.**
`gh api .../compare/eval/0.1.0-run28/main...eval/0.1.0-run28/develop` shows **zero** commits and **zero**
files different; fetching `specs/ROADMAP.md` from both branches byte-for-byte matches, including the
exact same garbled `v0.1.0` section. `main`'s commit log shows it's `develop`'s tip plus exactly one
commit: `run_eval_analysis.py`'s own end-of-run "Evaluation report" PR (`_open_and_merge_report_pr`,
which branches from whatever the local clone is currently on - `develop`, per `_sync_local_clone_to_branch`
- and merges into the run's `main`). That mechanism already carries `develop`'s full tree into `main` as
a side effect, every run, independent of whether the team's own `create_release_pr` (the SPRINT CLOSE
SEQUENCE's actual develop-to-main merge) ever succeeds. This run's sprint report never completed (no
`create_release_pr` call at all - the exact issue ISSUE-0046 targets), so there's no *team-driven*
develop-to-main sync to have gone missing; the harness's own report-PR mechanism already stood in for
it, coincidentally producing identical content on both branches.

**The real, verified cause: `update_roadmap` received a combined "ID: Title" string instead of a bare
ID, and its lookup only ever matched on exact equality.** Confirmed in the transcript - Product Owner
called:
```
update_roadmap(version="v0.1.0", stories=["US-0001: Create To-Do List", "US-0002: Add Task to List", ...])
```
`update_roadmap`'s lookup is `x.get("id") == s or x.get("title") == s` - `"US-0001: Create To-Do List"`
equals neither `product_backlog`'s id (`"US-0001"`) nor its title (`"Create To-Do List"`), so the match
fails for every story in the list. With no match, the code falls back to rendering the raw string as
both ID and title, with an empty `stages_completed` (every checkbox unchecked) - exactly the symptom
`0.1.0-run27` and `0.1.0-run28`'s reports both flagged, and unrelated to which branch anything lives on.

## Acceptance Criteria
- `update_roadmap`'s `stories` lookup resolves a leading ID token out of a combined "ID: Title"/
  "ID - Title"/"ID Title" string before matching, so a Product Owner passing IDs prefixed with their
  title (a predictable format for a cheap model to produce) still resolves to the real story, its real
  title, and its real `stages_completed` - not a permanently garbled, all-unchecked placeholder.
- A bare ID or a bare title-only string (no leading ID) continues to resolve exactly as before - no
  regression to the already-working case.
- Full `agents/scrum_team/tests` suite and top-level `pytest tests/` both pass with no regressions.
- No change to the sprint wrap-up/release-merge logic - investigation showed it's not the cause here (see
  Notes for a related but distinct gap this surfaced).

## Notes
- **Explicitly not implementing** "merge develop into main before creating the sprint/eval report" - the
  investigation shows this already effectively happens (via the eval-report PR's own branch-from-develop
  mechanics) whenever the team's own release merge doesn't. Adding a second, redundant merge step would
  not have changed this run's outcome (both branches were already identical) and isn't justified by what
  was actually observed. If a future run *does* show genuine develop/main divergence, that would point at
  a different, currently-unobserved gap - worth a fresh investigation against real evidence at that time,
  not a preemptive fix for a mechanism that's already working.
- This is a distinct root cause from ISSUE-0047's roadmap fix (`update_roadmap` tagging a matched
  story's `version` field) - that fix only takes effect once a match is *found*; this run's failure was
  upstream of that, in the match ever succeeding at all. Both fixes are complementary, not overlapping.
- `_resolve_story_ref`'s pattern intentionally requires the ID to be followed by whitespace (after an
  optional single separator character) - `"US-0001Foo"` (no separator/space at all) is deliberately left
  unresolved (falls through to the unchanged string) rather than risk mis-parsing a real title that
  happens to start with something ID-shaped.

## Test Approach
- `agents/scrum_team/tests/test_requirements.py::TestResolveStoryRef` - the helper resolves
  colon/dash/space-separated combined strings to the leading ID, and leaves a bare ID or bare title
  unchanged.
- `agents/scrum_team/tests/test_scrum.py::test_update_roadmap_resolves_a_combined_id_and_title_string` -
  an end-to-end regression reproducing the exact real call
  (`update_roadmap("v0.1.0", stories=["US-0001: Create To-Do List"])`), asserting the rendered section
  shows the real title and real (non-empty) checkboxes, and the story's `version` field still gets
  tagged (ISSUE-0047).
- Full `agents/scrum_team/tests` suite (via `docker compose --env-file .env.test run --rm --entrypoint ""
  -e PYTHONPATH=/app agent pytest agents/scrum_team/tests`, with `db`/`litellm` up first): 525 passed, no
  regressions.
- `pytest tests/`: 421 passed, no regressions.

## Resolution
- `agents/scrum_team/tools/requirements.py`: new `_resolve_story_ref(s)` helper and
  `_LEADING_ID_PATTERN`; `update_roadmap`'s two lookup sites (existing-version-section,
  new-version-section) resolve each `stories` entry through it before matching against
  `product_backlog`/`sprint_backlog`.
