# Issue

- Issue ID: SPEC-ISSUE-0085
- Title: `integrate_open_changes`'s `specs/`+`.hc/`-Only Path Scope Permanently Deadlocks Every Checkout Helper The Moment Any Other Tracked File (e.g. IDE/OS Metadata) Is Dirty, With No Tool Able To Recover
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-11-11

## Overview
Reported by the maintainer: a real sprint against the `decide-o-mat-KMP` project (local Ollama
install, `qwen2.5-coder:32b`, `.hc/state.json`'s `sprint_number: 2`) stopped repeatedly and had to be
restarted by hand multiple times. Investigated via that project's own `.hc/state.json`
(`blocking_interactions`, `decision_log`, `impediment_log`) and its git checkpoint history
(`git log -- .hc/state.json`), not this repo.

**Confirmed mechanism.** The target repo's working tree has two git-tracked files that get rewritten
just by having the project open in an IDE/Finder: `.idea/workspace.xml` (tracked from before
`.gitignore` added the (ineffective, for already-tracked files) `.idea/` rule) and `.DS_Store`. Every
sprint-lifecycle call that checks out `develop` - `create_story_spec_pr`/`create_sprint_backlog_pr`/
`start_feature_branch`/`create_release_pr`, all routed through the shared
`_checkout_develop_or_recover`/`_checkout_with_auto_integrate` helpers (`agents/scrum_team/tools/
github.py:147-177`, `:284-329`) - hit git's "local changes would be overwritten" refusal because of
these two dirty tracked files. The one and only self-heal path, `integrate_open_changes()`
(`github.py:75-144`), is **deliberately** scoped to just `_OPEN_CHANGES_PATHS = ["specs", ".hc"]`
(`github.py:72`) - by design, so it never sweeps DevTeam's own in-progress source edits into an
unrelated "planning docs" commit (see that constant's own comment). `.idea/workspace.xml`/`.DS_Store`
are neither `specs/.hc` planning output nor DevTeam source work - a third category the narrow scope
was never built to cover - so `integrate_open_changes()` finds its own pathspec clean, reports
`integrated: False`, the retry in `_checkout_with_auto_integrate` (`github.py:171-177`) never fires,
and the original "would be overwritten" error is returned to the caller unchanged, every single time.

No other recovery exists anywhere: `grep -rn "git stash\|git clean\|checkout -- \|reset --hard"` across
`agents/scrum_team/` returns zero hits - there is no tool any role can call to stash, discard, or
`.gitignore`-and-untrack an unrelated dirty file blocking a checkout.

The target repo's own `decision_log` shows ScrumMaster correctly diagnosing this, repeatedly, and
getting nowhere: *"The local changes in .idea/workspace.xml are preventing the checkout of the
'develop' branch. Committing these changes will allow the ScrumMaster to proceed..."* (tried, failed,
logged again, worded almost identically, at least 4 separate times), finally giving up with *"Manual
Intervention Required... Manually commit or stash the local changes in .idea/workspace.xml."*
Meanwhile `_detect_repeated_call_loop` (`agent.py:2226-2322`, threshold 3 at `agent.py:2160`) correctly
fired and soft-blocked the identical `start_sprint` retries this produced, and the transfer-loop
detector correctly fired on the resulting ProductOwner/ScrumMaster/ScrumOrchestrator ping-pong - both
guardrails did their job; neither has (or is meant to have) any way to fix the underlying blocker, only
to stop the agents from retrying it forever blindly.

The project's `.hc/state.json` checkpoint history (`git log --format='%ad %s' -- .hc/state.json`)
shows the team cycling through dozens of `save_state_to_repo` checkpoints across a ~2-hour span on
2026-11-10 (09:59-11:48) alone, on top of an earlier attempt on 2026-11-06, having spent 1,981,444
tokens (`token_usage.total`) - 1,301,055 of it on ScrumMaster alone - and still ending with an **empty
`sprint_backlog`**: not one story was ever actually planned into the sprint. Every `blocking_interactions`
entry from 2026-11-10 onward (ids 16-27) is this same checkout failure recurring under slightly
different tool-call shapes (`"Could not check out 'develop'"`, `"unable to commit local changes to
avoid branch conflicts"`, `"unable to check out 'develop' due to uncommitted local changes"`) - the
team never found a way out on its own in either attempt.

## Acceptance Criteria
- A checkout blocked only by dirty **tracked, non-source, IDE/OS-metadata-shaped** files (e.g.
  `.idea/*`, `.DS_Store`, `*.iml`, or any path already listed in the repo's own `.gitignore` that is
  nonetheless tracked) must be auto-recoverable without ever touching real DevTeam source edits -
  the existing `_OPEN_CHANGES_PATHS` safety guarantee (never sweep source work) must not regress.
- `integrate_open_changes` (or a new sibling helper reachable from
  `_checkout_with_auto_integrate`) must detect this specific case - a tracked path that is both (a)
  dirty and (b) matched by the repo's own `.gitignore` - and resolve it safely (e.g. `git update-index
  --skip-worktree` or `git rm --cached` + commit the removal, never a destructive discard of content
  the file might still legitimately hold) before the checkout retry.
- When the dirty blocker does **not** match this safe case (a real, unrecognized tracked-file
  conflict), the error surfaced back to the LLM must name the exact blocking file(s) (from `git status
  --porcelain`) rather than only the generic "would be overwritten" stderr, so the agent isn't left
  re-diagnosing the same thing from scratch on every retry.
- A new regression test: a repo with a tracked, `.gitignore`-matched file (e.g. `.idea/workspace.xml`)
  dirty on disk, calling any of the four checkout call sites - assert the checkout now succeeds without
  manual intervention and without the dirty file's real content being silently discarded.
- Must not regress `_preserve_local_only_develop_commits` (SPEC-ISSUE-0050) or the existing
  `specs/`+`.hc/` integration path - both must keep behaving exactly as before for repos that don't hit
  this specific case.

## Notes
- Sibling of SPEC-ISSUE-0050 (same `_checkout_develop_or_recover`/`_checkout_with_auto_integrate`
  call sites) and shares `integrate_open_changes`'s own documented incident (GH issue #399,
  `github.py:86-98`) - both are about this same checkout helper family having an incomplete picture of
  "what's safe to auto-resolve." This issue is the first to cover dirty files that are neither planning
  output nor source work.
- `_detect_repeated_call_loop` and the transfer-loop detector are working as designed here - they
  correctly prevented the team from burning its entire budget retrying a hopeless action forever, and
  correctly escalated to a human via `blocked_story`/`decision_log` entries. The gap is entirely that
  nothing - human instructions included, since the target repo's maintainer was not actively present to
  intervene - could actually clear the blocker; this issue is about giving the self-heal path enough
  scope to do so itself for the common, safe case.
- Project-specific but worth calling out to the reporting user directly (not a framework fix): the
  target repo's `.idea/` and `.DS_Store` were committed before `.gitignore` picked them up, so the
  ignore rule is currently a no-op for them (`git ls-files | grep '^\.idea'` still lists them). Running
  `git rm -r --cached .idea .DS_Store` once (and committing that) in `decide-o-mat-KMP` would resolve
  this specific recurrence immediately, independent of whether/when this framework fix lands - it stops
  the files from being tracked at all, so they can never again go dirty from the IDE/Finder churning
  them. Recommended as an immediate mitigation regardless of fix timeline.

## Test Approach
- `agents/scrum_team/tests/test_github.py`: new tests under whichever `integrate_open_changes`/
  `_checkout_with_auto_integrate` test class already exists - construct a temp repo, track a file
  matched by `.gitignore` (e.g. `.idea/workspace.xml`), dirty it, assert a checkout that previously
  failed with "would be overwritten" now succeeds; assert a genuinely untracked-by-gitignore dirty
  file still fails exactly as before (no change to the existing safety boundary).
- Add/extend a case alongside `_preserve_local_only_develop_commits`'s own existing tests confirming
  this new path doesn't interact badly with that function's merge/push/rescue-branch logic when both
  conditions occur in the same checkout attempt.

## Resolution
Added a new second recovery tier, `_untrack_gitignored_dirty_files` (`agents/scrum_team/tools/
github.py`), tried by `_checkout_with_auto_integrate` only after `integrate_open_changes` reports
nothing to integrate. It reads `git status --porcelain`, keeps only paths reported as **modified**
tracked files ("M"/"T" status - never "??" untracked, a different failure mode entirely), narrows that
down via `git check-ignore` to the subset actually matched by the repo's own `.gitignore`, and
`git rm --cached` + commits the removal for exactly those - never touching a dirty file that isn't
gitignore-matched (a real, unrecognized conflict is still left alone and still fails, same as before).
`git rm --cached` only removes the path from the index; the file's on-disk content is never discarded.

`_checkout_with_auto_integrate` now tries tier 1 (`integrate_open_changes`, unchanged), and only if
that reports `integrated: False`, tries tier 2 (`_untrack_gitignored_dirty_files`) and retries the
checkout again if it reports `integrated: True`. Both tiers share the same `"integrated"` boolean key
in their return dict, so every existing caller that reads `auto_integrated.get("integrated")` for its
own error message keeps working unchanged regardless of which tier fired. When neither tier finds
anything, the second tier's result also carries `dirty_tracked_files` (every modified tracked path it
saw, ignored-matched or not) so a caller that's still going to fail surfaces exactly what's blocking it.

Two existing tests encoded the now-fixed behavior as a contract ("if `integrate_open_changes` had
nothing to do, retrying would just fail identically, so don't bother") and were rewritten to reflect
that a second tier now gets a chance first: `TestCheckoutWithAutoIntegrate.
test_gives_up_when_neither_recovery_tier_finds_anything` / `TestCheckoutDevelopOrRecover.
test_gives_up_if_neither_recovery_tier_finds_anything` (renamed from their "...when_integration_finds_
nothing..." originals), plus one new "self-heals via the untrack tier" test added alongside each.

Tests added:
- `test_github.py`'s new `TestUntrackGitignoredDirtyFiles` class (7 tests): clean no-op, ignores
  untracked ("??") files entirely, leaves a non-gitignored dirty tracked file alone, untracks+commits a
  gitignored one, only touches the gitignored subset when several files are dirty, confirms no
  `clean`/`reset`/`checkout --` ever runs against the blocking file (content never discarded), and
  surfaces an error if `git rm --cached` itself fails.
- `TestCheckoutWithAutoIntegrate`/`TestCheckoutDevelopOrRecover`: one new test each for the
  self-heal-via-untrack path, alongside the two rewritten give-up tests above.

Full `agents/scrum_team/tests` suite (via `docker compose -p horseless-carriage-test run --rm
--entrypoint "" agent pytest`, same project-scoped invocation `run_tests.py` uses): 1256 passed, no
regressions.
