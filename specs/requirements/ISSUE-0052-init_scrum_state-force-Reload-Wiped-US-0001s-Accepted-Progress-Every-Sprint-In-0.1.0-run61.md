# Issue

- Issue ID: ISSUE-0052
- Title: `init_scrum_state` Must Not Be An Agent-Callable Tool - Exposing It Let ProductOwner's `force=True` Wipe US-0001's Accepted Progress Every Sprint In `0.1.0-run61`
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

**This is a recurrence, not a first occurrence - and it pins down exactly what's still missing.**
`init_scrum_state`'s own docstring documents a near-identical prior incident, `0.1.0-run56` (GH issue
#423): "ScrumMaster re-called `init_scrum_state()` at the start of Sprint 2 ... and three stories that
had already genuinely reached Accepted in Sprint 1 came back out of that call showing only
Draft/Ready." The fix that shipped for #423 was the `force or not already_initialized` gate itself -
which *did* close the accidental/habitual re-init case (an agent calling the tool again without
realizing it already ran). It deliberately left `force=True` open "for the rare genuine case of
deliberately picking up an out-of-band edit to state.json mid-session" - a bar no in-session agent call
can actually clear (an agent has no way to know whether a human edited `.hc/state.json` out-of-band;
it can only ever be guessing), yet the tool remains callable by every role with that argument exposed.
`0.1.0-run61` is simply the next time an agent reached for it: same mechanism, same blind overwrite,
same regression, now via the one door GH #423 left open on purpose.

**Root cause of the root cause: `init_scrum_state` should never have been an LLM-callable tool at
all.** A mechanical, non-agent caller already exists and already runs it correctly exactly once per
session - `ensure_state_initialized_callback` (`agents/scrum_team/agent.py:798`), a `BeforeModelCallback`
registered on `root_agent`, calls `init_scrum_state()` (never with `force=True`) before the
Orchestrator's very first turn, gated on the same `_state_auto_initialized` flag. That callback is the
correct "mechanical setup" path end to end. The only reason `0.1.0-run61`'s ProductOwner could re-run
initialization mid-session at all is that `init_scrum_state` is *also* separately registered as a
directly-callable tool on every one of the six agents - `ProductOwner`, `ScrumMaster`, `DevTeam`, `QA`,
`Architect`, and `ScrumOrchestrator` itself (`agents/scrum_team/agent.py:2667, 2703, 2742, 2777, 2798,
2823`). Nothing about that registration is load-bearing: the callback already guarantees state is
initialized before any role's first real turn, so no agent ever legitimately *needs* to call this tool
itself - its presence in six tool lists is pure, unnecessary surface area for exactly this failure.

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
- `init_scrum_state` is removed from every agent's tool list in `agents/scrum_team/agent.py`
  (`ProductOwner`, `ScrumMaster`, `DevTeam`, `QA`, `Architect`, `ScrumOrchestrator`) - no role can
  invoke it directly, with or without `force`, ever again. The function itself stays (it's still the
  mechanical callback's and the repair path's implementation), only its exposure as an LLM tool goes.
- `ensure_state_initialized_callback` keeps calling `init_scrum_state()` exactly once per session,
  unchanged, `force` never set - this is already the correct "mechanical setup, not an agent action"
  path (it runs before the Orchestrator's first real turn, gated on `_state_auto_initialized`) and
  needs no behavior change, only to remain the *only* path into this function once the tool-list
  entries above are gone.
- The genuine "pick up an out-of-band edit to `.hc/state.json`" / "state is corrupted beyond git-history
  recovery" case - the one `force=True` was actually meant for - gets a real, human-operated way to
  invoke it, matching the design this codebase already uses for exactly this class of recovery (see
  `docs/STATE-REPOSITORY.md` "If automatic recovery isn't possible either": `get_corrupted_state_raw_content`/
  `save_repaired_state`/`reset_state_from_git`/`clear_corrupted_state` are all in-chat tools that
  "refuse to run against a state.json that's currently perfectly fine - they only ever act on a
  genuinely corrupted file, never as a way to reset good state"). `check_state_repo.py`'s
  `_offer_state_repair()` menu already mirrors two of those host-side (reset-from-git-history,
  delete-and-reinit-next-session) but has no equivalent to `init_scrum_state`'s doc-sync step - rebuild
  `product_backlog`/`sprint_backlog`/vision/goals/architecture from whatever `specs/` already has on
  disk - which is the one thing neither a git-history reset nor a delete-and-restart actually provides
  (both only recover/clear `state.json` itself; neither re-derives backlog content from the markdown
  specs). Add that as a genuinely new last-resort option - e.g. "4) Rebuild state from specs/ on disk"
  in `check_state_repo.py` (host-side, invoked the same way `validate_state.py` already is - via
  `docker compose run --rm agent python3 -m ...` so pydantic/ADK are available), and/or a narrow
  `rebuild_scrum_state_from_specs()` in-chat tool that - like the four corruption-only tools above -
  refuses to run unless `state_json_corrupted`/equivalent is actually set. Either way, this is reachable
  only when the state is already known-bad, never a routine, always-available agent action.
- A story's `stages_completed` regressing on any load (the actual damage mechanism, independent of who
  triggers it) is still worth guarding monotonically at the `load_state_from_repo` level as defense in
  depth, but it's no longer the primary fix - removing the tool exposure above means no agent can
  trigger a reload at all, so there's no longer a live path by which a cheap model's own choice can
  overwrite live progress mid-session.

## Notes
- This supersedes the originally-proposed fix shape here (a monotonic merge inside
  `load_state_from_repo`). That would have papered over the real problem: the tool is only dangerous
  *because* it's reachable from a role's own turn, and `force=True` exists specifically to let an
  agent override the one safety gate (`_state_auto_initialized`) protecting against exactly this.
  Removing the tool from every role's list closes the door this run (and `0.1.0-run56`/GH #423 before
  it) walked through, rather than making the room on the other side of that door slightly safer.
- This is a distinct mechanism from ISSUE-0050 (`_checkout_develop_or_recover`'s `checkout -B` hard
  reset discarding *local git commits*) even though the observable symptom rhymes closely ("a story's
  `stages_completed` regressed between sprints") - ISSUE-0050's fix doesn't cover this path at all,
  since `load_state_from_repo`'s overwrite happens in Python/session-state, not via that git helper.
- Whether `.hc/state.json` on disk was actually stale because of `_sync_local_clone_to_branch`
  (`agents/scrum_team/scripts/run_eval.py`) performing its `fetch`/`checkout`/`pull --ff-only` with zero
  status checking on any of the three calls - so a failed `--ff-only` pull there would be silently
  swallowed - wasn't pinned down further here, and doesn't need to be to close this issue: once
  `init_scrum_state` can't be agent-triggered mid-session, whatever's on disk between sprints is
  irrelevant to live session state either way. Worth a quick look as a follow-up, independent of this
  fix.
- `0.1.0-run56`'s GH #423 fix already correctly reasoned "nothing stopped an agent from calling
  `init_scrum_state()` again later in an already-live session" and then only half-closed that gap (the
  non-force path). This issue is that same sentence's other half.

## Test Approach
- `agents/scrum_team/tests/test_agent.py`: new assertion that `init_scrum_state` does not appear in
  the `tools` list of any of `product_owner`, `scrum_master`, `dev_team`, `qa_agent`, `architect`,
  `root_agent` - a structural test that fails immediately if it's ever re-added to a role's tool list,
  not just a behavioral test that happens to not exercise it.
- Existing `ensure_state_initialized_callback` tests (`test_agent.py`, around
  `test_calls_init_scrum_state_on_first_call`/`test_does_not_call_init_scrum_state_again_once_flagged`)
  should pass unchanged - this issue doesn't touch that callback.
- New test for `check_state_repo.py`'s added repair-menu option: given a corrupted/unrecoverable
  `.hc/state.json`, choosing the new option invokes the rebuild step and leaves a valid state.json
  behind, exercised the same way `_offer_state_repair`'s existing options are already tested (an
  injectable `prompt` callable, no real terminal needed).
- If a `rebuild_scrum_state_from_specs()` in-chat tool is added too: tests mirroring
  `test_scrum.py`'s existing coverage of `clear_corrupted_state`/`reset_state_from_git`'s "refuses
  unless `state_json_corrupted`" guard - both that it refuses against currently-good state, and that it
  actually rebuilds (stories/epics/vision/goals synced) when state is genuinely corrupted.
- Full `agents/scrum_team/tests` suite should otherwise pass unchanged - `init_scrum_state` itself and
  `load_state_from_repo` keep their current signatures and behavior; only where they're reachable from
  changes.
