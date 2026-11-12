# Issue

- Issue ID: SPEC-ISSUE-0088
- Title: `create_release_pr` Deadlocks, Retrying Forever, When Its Own `generate_changelog` Write Leaves `CHANGELOG.md` Dirty Across A Later Checkout
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-11-11

## Overview
Reported by `0.2.0-run90`'s `report.md`: the run stopped after 2 of 5 requested sprints -
`Stop reason: budget_critical_halt`. Sprint 2 alone burned ~5.5M of its 5M token budget (total
across both sprints: 11,545,369) despite its own `ScrumMaster`-generated sprint report (logged
mid-sprint, `transcript.md:10724`) showing only 2,664,778 tokens used and "All planned stories were
completed within budget - no sprint-length change suggested." Nearly all of the remaining ~2.9M
tokens were burned entirely *after* that report, retrying `create_release_pr` in an identical,
never-resolving loop.

**Confirmed mechanism.** `create_release_pr` (`agents/scrum_team/tools/github.py:1865`) lands the
sprint report/transcript on `develop`, then (`:1946-1952`) calls `generate_changelog`, which writes
`CHANGELOG.md` to disk **uncommitted** (`tools/changelog.py:88-118`, via `write_file`). The very next
step, `integrate_open_changes()` (`:1961`), only ever stages `specs/` and `.hc/`
(`_OPEN_CHANGES_PATHS`, `github.py:72`) - a root-level file like `CHANGELOG.md` is out of scope, so
it's never committed here. `_git_push_impl(branch=develop, add_all=False, ...)` (`:1962-1968`) then
re-fetches and re-checks-out `develop` internally (protected-branch path), via the shared
`_checkout_with_auto_integrate` helper (`github.py:233-280`) - and because the only dirty file is
`CHANGELOG.md`, neither of that helper's two recovery tiers fires (tier 1, `integrate_open_changes`,
is out of scope per above; tier 2, `_untrack_gitignored_dirty_files` added by SPEC-ISSUE-0085, only
acts on a dirty file also matched by `.gitignore` - `CHANGELOG.md` is deliberately tracked, never
ignored). The checkout fails with git's raw, unrecovered stderr:
```
Could not check out branch 'develop': error: Your local changes to the following files would be
overwritten by checkout:
	CHANGELOG.md
Please commit your changes or stash them before you switch branches.
Aborting
```
(`transcript.md:10783`, `:10851`, `:10953`, `:11035`, repeated verbatim at least 33 times across the
sprint per `grep -c "would be overwritten by checkout" transcript.md`).

Each time, `create_release_pr` re-wraps this as `"Failed to land the sprint report/transcript on
develop before opening the release PR."` (`github.py:1969-1974`). `ProductOwner` calls
`raise_story_blocker` on `US-0004` (`transcript.md:11074-11081`); `Architect` calls
`resolve_story_blocker` with a free-text resolution ("uncommitted tracked changes can be resolved by
stashing or committing them before invoking `create_release_pr`", `transcript.md:11124`) which
**unconditionally clears the `BLOCKED` flag** (`requirements.py:2927-2968`) - `resolve_story_blocker`
only has a mechanical recheck for one narrow category (`mechanical_recheck.kind ==
"tested_stage_failure"`, attached solely by `advance_story_stage`'s own `Tested`-stage rejection path,
`requirements.py:133-171`); this blocker, raised directly via `raise_story_blocker` over a
`create_release_pr` failure, never gets one, so any resolution string passing the generic
`_is_concrete_denial_reason` length/placeholder heuristic (`requirements.py:194-211`) clears it. The
"resolution" never touches the actual working tree - `CHANGELOG.md` is still dirty - so
`ProductOwner`'s immediate retry of `create_release_pr` hits the byte-for-byte identical git error,
and the cycle repeats until the sprint's token budget is exhausted.

This is a different failure from SPEC-ISSUE-0051 (a *commit*-step message-matching gap, already
fixed, confirmed still in place at `github.py:635-640`) and from SPEC-ISSUE-0085 (a *checkout*-step
gap, but only for `.gitignore`-matched files) - this is a *fourth*, still-uncovered category: a
tracked, non-ignored file that `create_release_pr` itself dirties mid-flight, in the gap between its
own `generate_changelog` write and its own later re-checkout.

**Unconfirmed secondary observation:** both `0.2.0-run90`'s and `0.2.0-run91`'s transcripts show the
exact same `_untrack_gitignored_dirty_files` response shape with `"dirty_tracked_files":
["HANGELOG.md"]` - missing the leading "C" from "CHANGELOG.md" - identically in two independent runs
(e.g. `0.2.0-run91` transcript, Event 122's `auto_integrated` sub-result). This is consistent, real
evidence of a truncation somewhere, but reading `_untrack_gitignored_dirty_files`
(`github.py:147-230`) and hand-tracing its `code, path = line[:2], line[3:]` porcelain-line slicing
against a real `git status --porcelain` line for a dirty `CHANGELOG.md` does not reproduce it (both
unstaged `" M CHANGELOG.md"` and staged `"M  CHANGELOG.md"` forms slice correctly to
`"CHANGELOG.md"`). Flagging for whoever investigates the next run where this recurs - worth capturing
a raw, untruncated `git status --porcelain` line from inside a live container next time, since neither
this investigation's static read of the code nor a local hand-trace explains the exact byte dropped.

## Acceptance Criteria
- A dirty `CHANGELOG.md` (or any other root-level file `generate_changelog` writes) no longer blocks
  the re-checkout inside `_git_push_impl`'s protected-branch path when `create_release_pr` calls it -
  either by committing `CHANGELOG.md` immediately after `generate_changelog` succeeds (before any
  further checkout can observe it dirty), or by widening `_OPEN_CHANGES_PATHS`/the self-heal chain to
  cover it specifically, since it is framework-generated content `generate_changelog` is the only
  writer of - never DevTeam's uncommitted source work (the exact risk `_OPEN_CHANGES_PATHS`'s own
  comment warns against sweeping).
- A new regression test reproduces `0.2.0-run90`'s exact shape: `generate_changelog` leaves
  `CHANGELOG.md` dirty, then a checkout that would otherwise fail with "would be overwritten by
  checkout: CHANGELOG.md" instead succeeds (or the dirty file is committed out of the way first) -
  asserting `create_release_pr` does not return the "Failed to land the sprint report/transcript..."
  error for this specific cause.
- Does not weaken `_OPEN_CHANGES_PATHS`'s existing guarantee that DevTeam's own uncommitted source
  edits are never auto-swept - the fix must be scoped specifically to `CHANGELOG.md` (or files
  `generate_changelog` itself writes), not a general `add_all=True`.

## Notes
- Gap lives in `agents/scrum_team/tools/github.py`: `_OPEN_CHANGES_PATHS` (`:72`) and/or
  `create_release_pr`'s changelog-then-push sequence (`:1940-1968`).
- `resolve_story_blocker`'s lack of a mechanical recheck for this blocker category
  (`agents/scrum_team/tools/requirements.py:2927-2968`) is a secondary contributing factor - it let
  the team "resolve" the blocker on narrative text alone, over and over, with no connection to real
  git state - but is not the root cause; even a correctly-gated `resolve_story_blocker` would still
  loop forever without the underlying checkout ever being able to succeed. Not filing a second issue
  for this since fixing the root cause (this issue) removes the only call site that would ever raise
  this particular blocker in the first place; worth a mechanical-recheck sibling (a `kind` that
  actually re-checks working-tree git status) only if a *different* future blocker category exposes
  the same "any text clears it" gap on its own.
- Investigated via `gh run view 43051886091`, its `eval-report-0.2.0-run90` artifact
  (`report.md`/`manifest.json`/`transcript.md`), and reading `agents/scrum_team/tools/github.py`/
  `requirements.py`/`changelog.py` directly - no story-stage regression in `manifest.json` (US-0004
  did eventually reach `Accepted` by end of Sprint 2, despite burning the budget getting there); the
  damage is purely the ~2.9M tokens wasted on the retry loop that then starved US-0005/US-0006 of any
  sprint time at all.

## Test Approach
- `agents/scrum_team/tests/test_github.py`: a case that writes a dirty, tracked `CHANGELOG.md` (e.g.
  via `generate_changelog` or a direct write) differing from `origin/<develop>`'s committed copy, then
  calls `create_release_pr` (or directly `_checkout_with_auto_integrate`/`integrate_open_changes`) and
  asserts the checkout self-heals and succeeds, rather than returning the "would be overwritten by
  checkout" failure.
- Confirm the fix does NOT sweep a dirty file outside `specs/`/`.hc/`/`CHANGELOG.md` (e.g. a fake
  dirty `app.py`) - that case must still fail exactly as before, preserving the existing DevTeam
  source-work protection.

## Resolution
`_OPEN_CHANGES_PATHS` (`agents/scrum_team/tools/github.py:72`) now includes `CHANGELOG.md` alongside
`specs`/`.hc` - `integrate_open_changes` (tier 1 of `_checkout_with_auto_integrate`'s self-heal chain)
stages and commits a dirty `CHANGELOG.md` the same way it already did for planning-doc writes, so a
later re-checkout (the one `_git_push_impl`'s protected-branch path runs inside `create_release_pr`)
never finds it dirty in the first place. This is safe because `generate_changelog` is `CHANGELOG.md`'s
only writer anywhere in this codebase - never DevTeam's uncommitted source work, the exact risk
`_OPEN_CHANGES_PATHS`'s scoping exists to guard against - and because every pathspec-scoped git
command (`git status --porcelain --`, `git add --`) this function runs is structurally incapable of
touching any file outside its explicit path list, so no other dirty file is ever newly swept.

`agents/scrum_team/tests/test_github.py`: `test_commits_only_specs_and_hc` updated for the new
3-pathspec `git add` invocation; new `test_commits_a_dirty_changelog_md` directly replays the fix -
a dirty, tracked `CHANGELOG.md` is staged and committed by `integrate_open_changes` exactly like a
dirty `specs/ROADMAP.md` already was.

Full suite (`python3 run_tests.py`, host suite + `agents/scrum_team/tests` via Docker Compose) passes:
798 host tests + 1265 agent-suite tests (138 subtests), zero failures, including both the updated and
new tests.
