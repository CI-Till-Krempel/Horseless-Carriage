# Issue

- Issue ID: ISSUE-0052
- Title: `init_scrum_state(force=True)` At Every Sprint Start Overwrote `product_backlog` With A Stale `.hc/state.json` Snapshot, Permanently Un-Accepting US-0001 From Sprint 2 Onward In `0.1.0-run61`
- Status: Ready
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-13

## Overview
Reported (maintainer, reviewing `0.1.0-run61`, GH Actions run 38381467120): `EVAL-REPORT.md` shows
Team Efficiency at 1/5 - only 1 of 6 user stories (US-0001) was ever implemented/accepted, all in
Sprint 1; Sprints 2 through 5 recorded zero planned or completed stories ("No sprint backlog
recorded"), burning 10.17M tokens across 5 sprints for that single story.

Investigated directly against the real run (artifact `eval-report-0.1.0-run61`'s `manifest.json`/
`transcript.md`, plus `CI-Till-Krempel/horseless-carriage-eval-todo-app` PRs for `eval/0.1.0-run61/*`).

**Confirmed via `manifest.json`'s own per-sprint `product_backlog` snapshots:** US-0001's
`stages_completed` was `["Draft", "Ready", "Implemented", "Reviewed", "Tested", "Accepted"]` at the
end of Sprint 1 (matching Sprint 1's own report: "Successfully delivered and accepted US-0001"), then
`["Draft", "Ready"]` - Implemented/Reviewed/Tested/Accepted all gone - in every single sprint snapshot
from Sprint 2 through Sprint 5. Not a one-time regression (contrast ISSUE-0050, where a story regressed
one sprint and the team could in principle recover): it never moves again for the rest of the run,
because the same mechanism re-fires every sprint.

**Root cause, confirmed directly in `transcript.md`:** ProductOwner calls `init_scrum_state(force=True)`
as its first action of every sprint from Sprint 2 onward (events at Sprint 2/3/4/5's very start -
`transcript.md` lines 4045, 5171, 6189, 7241 of this run's artifact), and every single call responds
`"reloaded_from_disk": true, "already_initialized": true`. `init_scrum_state`
(`agents/scrum_team/tools/scrum.py`) only reloads `.hc/state.json` from the git working tree when
`force or not already_initialized` - for an already-running session (`already_initialized` true for
every one of these calls), that means only `force=True` itself triggers it. `load_state_from_repo`
(same file) then does a **blind, unconditional overwrite**: `for k, v in data.items():
tool_context.state[k] = v` - every key in the on-disk snapshot replaces the in-memory value outright,
including `product_backlog`, with no merge and no check for whether the in-memory copy is actually
further along.

This directly contradicts `init_scrum_state`'s own documented invariant (its code comment, same
function, just above the `force or not already_initialized` gate): *"live in-memory state is always at
least as current as whatever snapshot happens to be sitting in `.hc/state.json` on disk, so reloading
here can only ever regress progress, never recover it"* - which is exactly why the reload is normally
gated to a session's first init only. `force=True` is the one path around that gate, and nothing stops
an agent from calling it on every sprint, which `0.1.0-run61`'s ProductOwner did. Whatever was on disk
at each of those moments (most likely: US-0001's `Accepted`-stage `save_state_to_repo` checkpoint
commit was never a fast-forward ancestor of whatever `origin/develop` the local clone's `.hc/state.json`
was read from at that instant - the same class of local-vs-origin divergence ISSUE-0050 fixed for
`_checkout_develop_or_recover`, but `run_eval.py`'s own `_sync_local_clone_to_branch`, used to sync the
clone "between sprints", performs its `fetch`/`checkout`/`pull --ff-only` with zero status checking on
any of the three calls, so a failed `--ff-only` pull there is silently swallowed rather than surfaced)
never had the later stages recorded, and this call re-asserted that stale snapshot over the richer
in-memory state every time, permanently.

**Downstream effect, also confirmed in `transcript.md`:** immediately after the Sprint 2 reload,
DevTeam's `start_feature_branch("US-0002", ...)` is rejected: `"Cannot start work on 'US-0002' - the
higher-priority story 'US-0001' must reach Accepted first."` (same rejection recurs identically in
Sprint 3). This is `start_feature_branch`'s own priority-order gate
(`agents/scrum_team/tools/github.py:759`, mirrored in `advance_story_stage`,
`agents/scrum_team/tools/requirements.py:1524`) working exactly as designed against now-corrupted state
- the gate isn't the bug, the state it's reading is. DevTeam never retries or escalates; the sprint
falls straight through to retro/report with the story backlog untouched. Sprints 4 and 5 then never
even get a backlog published at all (`create_sprint_backlog_pr` rejected both times: "there's no new
planning output ... to publish this sprint yet" - ProductOwner stopped trying to re-plan once replanning
kept hitting the same wall).

## Acceptance Criteria
- `init_scrum_state(force=True)` (or `load_state_from_repo` directly) never regresses a story's
  `stages_completed` (in `product_backlog` or `sprint_backlog`) to fewer completed stages than what's
  already in live session state - the merge must be monotonic (same union semantics
  `_story_stages_completed` already uses for *reading* stage progress, applied here when *loading* it),
  not a blind per-key overwrite.
- A test reproducing this run's exact shape: seed in-memory `state["product_backlog"]` with US-0001 at
  `Accepted`, write a `.hc/state.json` on disk with US-0001 only at `Ready` (simulating a lagging/stale
  snapshot), call `init_scrum_state(force=True)`, and assert US-0001's `stages_completed` in the
  resulting state still includes `Accepted` afterward.
- `_sync_local_clone_to_branch` (`agents/scrum_team/scripts/run_eval.py`) checks the status of its
  `fetch`/`checkout`/`pull --ff-only` calls and at minimum logs (to the sprint's own event stream, not
  just stderr) when the pull wasn't a clean fast-forward, so a divergence like this is visible in the
  run's own report/transcript instead of only discoverable by diffing `manifest.json` snapshots by hand.

## Notes
- Not proposing to remove the `force` parameter or the reload path entirely - `init_scrum_state`'s
  own docstring frames it as a deliberate, if dangerous, escape hatch (and `force=False`'s
  first-init-only gating is correct and should stay). The fix is making the reload itself safe
  (monotonic merge) rather than relying on agents never calling `force=True` after the first init -
  the same "enforce in code, not by asking nicely in a prompt" principle this codebase already applies
  to `advance_story_stage`'s own ordering/ownership gates.
- This is a distinct mechanism from ISSUE-0050 (`_checkout_develop_or_recover`'s `checkout -B` hard
  reset discarding *local git commits*) even though the observable symptom rhymes closely ("a story's
  `stages_completed` regressed between sprints") - ISSUE-0050's fix doesn't cover this path at all,
  since `load_state_from_repo`'s overwrite happens in Python/session-state, not via that git helper.
  Whether `.hc/state.json` on disk was stale because of `_sync_local_clone_to_branch`'s unchecked
  `pull --ff-only`, or some other sync gap, is secondary - even a perfectly fresh on-disk snapshot
  should never be allowed to regress progress on load, per `init_scrum_state`'s own stated invariant.
- Likely why this wasn't caught before: `0.1.0-run61` is the first run (of the runs checked) where
  ProductOwner's own turn pattern includes `init_scrum_state(force=True)` as the very first tool call of
  every sprint, not just the first. Whether that's a new prompt nudge or the cheap model's own habit
  wasn't determined here; either way the tool itself should not be one wrong agent choice away from
  silently erasing a sprint's worth of delivered work.

## Test Approach
- Unit test in `agents/scrum_team/tests/test_scrum.py` (or wherever `init_scrum_state`/
  `load_state_from_repo` are currently tested): the regression scenario in Acceptance Criteria above,
  plus a check that a *forward* disk snapshot (on-disk ahead of memory - the genuine recovery case
  `force=True` exists for) still loads normally.
- Full `agents/scrum_team/tests` suite should pass unchanged otherwise - this only changes
  `load_state_from_repo`'s merge behavior, not `init_scrum_state`'s other doc-sync/env-override steps.
