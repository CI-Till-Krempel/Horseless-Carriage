# Issue

- Issue ID: SPEC-ISSUE-0072
- Title: `sprint_backlog` Is Never Pruned Of A Prior Sprint's Already-`Accepted` Items, Letting A New Sprint Skip Straight From Planning To Review/Retro/Release
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-27

## Overview
Reported while investigating `0.1.0-run74`: the maintainer noticed Sprint 2's phase jumped from
`"Plan & Start Sprint"` straight to `"Review, Retro & Release"`, skipping `"Development"` entirely -
"this should not be possible... we always need to follow the order of these phases without skips."

**Confirmed mechanism.** `manifest.json`'s own per-sprint snapshots show `sprint_backlog` for Sprint 2
is byte-identical to Sprint 1's - still only `US-0001`/`US-0002`, both already
`['Draft','Ready','Implemented','Reviewed','Tested','Accepted']`. `current_sprint_phase`
(`agents/scrum_team/helpers.py:887-915`) only reaches `"Review, Retro & Release"` (vs. `"Development"`)
via its `all_accepted` check (`helpers.py:902-904`): `bool(sprint_stories) and all("Accepted" in
stages for x in sprint_stories)`. For Sprint 1's own first-ever `create_sprint_backlog_pr` success,
`sprint_backlog` legitimately started empty (`ScrumState`'s own default), so `all_accepted` was
naturally `False` (`bool([])` is `False`) and phase correctly fell through to `"Development"`. **From
Sprint 2 onward, nothing ever clears `sprint_backlog` between sprints** - so the moment Sprint 2's own
`create_sprint_backlog_pr` merged (`transcript.md:328-333`: `"status": "ok", "merged": true,
"sprint_number": 2`), `all_accepted` evaluated against the *same* two already-`Accepted` leftovers from
Sprint 1 and came back trivially `True` - there was no new, not-yet-`Accepted` item in `sprint_backlog`
for `"Development"` to ever apply to.

**Why nothing new ever landed in `sprint_backlog` for Sprint 2 - and why that's not simply "the agent
forgot."** The only tool that writes `sprint_backlog` at all is `plan_sprint_backlog_item`
(`agents/scrum_team/tools/scrum.py:1010-1048`) - and it is **exclusively on DevTeam's tool list**
(`agent.py:2871`), absent from ProductOwner's (`agent.py:2797-2823`). Yet
`create_sprint_backlog_pr`'s own `unaddressed_must_issues` rejection (`github.py:964-967`, the one
ProductOwner actually hit in this run at `transcript.md:141-145`) tells the *calling role*
(ProductOwner) to "Call advance_story_stage(id_or_title, 'Ready') for each one (then
`plan_sprint_backlog_item` to actually queue it for this sprint)..." - a tool ProductOwner cannot call
at all. ProductOwner did exactly the first, reachable half
(`transcript.md:236-296`: `upsert_issue`/`advance_story_stage(..., 'Ready')` for `SPEC-ISSUE-0001`)
and then retried `create_sprint_backlog_pr` directly, which succeeded on a PR body that merely
*claimed* 5 new stories (`transcript.md:319-324`) with nothing mechanically checking it against
`sprint_backlog`'s real contents - the same unreachable-tool-in-a-rejection-message shape as
SPEC-ISSUE-0068, just for a different gate.

The *real*, working design (confirmed by Sprint 1's own successful flow, `transcript.md:1829`
onward) has DevTeam discover "not planned yet" itself once it tries to start work after the Sprint
Backlog PR merges (`"Cannot start work on 'US-0001' - it hasn't been planned into this sprint yet.
Call plan_sprint_backlog_item(...) first"`), then calls `plan_sprint_backlog_item` (initially refused
by `PHASE_TOOL_POLICY` since the phase has already flipped to `"Development"`, then let through via
the gate's own documented immediate-retry override - `transcript.md:1869-1913`). **Sprint 2 never
reached this step at all**, because phase skipped straight past `"Development"` before DevTeam ever
got a turn to try starting work and trip the "not planned yet" nudge that normally drives
`plan_sprint_backlog_item` being called.

This surfaced in this exact run only because SPEC-ISSUE-0070's fix (already merged, this run's commit
`f071e99` is a descendant) now correctly lets Sprint 2's `start_sprint` succeed and the team actually
reach sprint-planning again - previously the team never got far enough into a second sprint for this
gap to matter. It is a distinct, sibling mechanical gap: SPEC-ISSUE-0070 fixed `sprint_number`/phase
advancing at all; this issue is about `sprint_backlog`'s own stale content never being invalidated
once that advance happens.

## Acceptance Criteria
- The moment a sprint's own `create_sprint_backlog_pr` call *first* succeeds for a new `sprint_number`
  (i.e. exactly where it already detects `sprint_backlog_pr_sprint != sprint_number` to snapshot
  `sprint_backlog_engagement_baseline`, `github.py:1099-1103` and `github.py:1176-1187`),
  `sprint_backlog` must drop every item that already reached `Accepted` - those are a prior, already-
  fully-delivered sprint's business, not this one's. Items *not yet* `Accepted` (genuinely still in
  progress, legitimately spanning a sprint boundary) must be kept, not discarded.
- After this prune, a sprint whose `sprint_backlog` is now empty (no in-progress carry-over, nothing
  queued yet) must report `"Development"`, not `"Review, Retro & Release"`, for `current_sprint_phase`
  - matching Sprint 1's own correct behavior with a genuinely-empty `sprint_backlog`.
- Does not reopen SPEC-ISSUE-0070: `sprint_backlog_pr_sprint` itself must still correctly track
  `sprint_number` so `sprint_backlog_pr_missing` keeps working as that issue fixed it.
- Does not regress the legitimate "sprint genuinely finished, time to close out" case: once DevTeam
  queues and completes real work this sprint and every item in `sprint_backlog` reaches `Accepted`,
  `current_sprint_phase` must still correctly report `"Review, Retro & Release"`.
- A new regression test replaying this run's exact shape: `sprint_backlog` holds only a prior sprint's
  fully-`Accepted` items, `create_sprint_backlog_pr` succeeds for a new `sprint_number` - asserts
  `sprint_backlog` no longer contains those stale items afterward, and that `current_sprint_phase`
  reports `"Development"`, not `"Review, Retro & Release"`.

## Notes
- `ready_backlog_shortfall` (also checked by `create_sprint_backlog_pr`) is about `product_backlog`'s
  overall future depth - how much Ready work exists across *all* sprints - not whether anything was
  actually pulled into *this* sprint's `sprint_backlog`. These are orthogonal checks; this issue is
  about the latter.
- The pre-existing `unaddressed_must_issues` rejection message (`github.py:964-967`) still names
  `plan_sprint_backlog_item` as something the calling role (ProductOwner, or CEO/EVAL-level Architect)
  should call directly - unreachable for any role but DevTeam, the same gap SPEC-ISSUE-0068 closed for
  `resolve_story_blocker`. Not fixed here (this issue's fix makes the specific symptom observed in
  `0.1.0-run74` impossible without touching that message), but worth a follow-up issue if a future run
  shows a role actually getting stuck on it rather than working around it as ProductOwner did here.
- This fix does not need (and deliberately avoids) a new refusal gate on `create_sprint_backlog_pr`
  itself - pruning stale `Accepted` items lets the *existing*, already-working Sprint 1 mechanism
  (DevTeam discovers "not planned yet" and self-serves via `plan_sprint_backlog_item`) naturally kick
  in for Sprint 2 onward too, rather than introducing a second path that would also need its own
  reachable-role wording.

## Test Approach
- `agents/scrum_team/tests/test_github.py`: construct state with `sprint_number` about to advance to a
  new value, `sprint_backlog` holding only items already `Accepted` from the prior sprint - call
  `create_sprint_backlog_pr` (mocking the git/gh boundary the same way existing tests in this file
  do) and assert `sprint_backlog` is empty (or contains only genuinely not-yet-`Accepted` items)
  afterward.
- Add a mixed case: `sprint_backlog` holding one `Accepted` item and one still-`Implemented` (not yet
  `Accepted`) item - assert only the `Accepted` one is dropped, confirming legitimate in-progress
  carry-over survives.
- `agents/scrum_team/tests/test_helpers.py` (`TestCurrentSprintPhase`): confirm (already covered by
  SPEC-ISSUE-0070's own regression test, `test_plan_and_start_sprint_once_this_sprints_release_has_actually_gone_out`)
  that an empty `sprint_backlog` plus a matching `sprint_backlog_pr_sprint` reports `"Plan & Start
  Sprint"` pre-publish and `"Development"` post-publish - add the post-publish case explicitly if not
  already covered.
- Replay `0.1.0-run74` Sprint 2's shape (or an equivalent constructed scenario) against the fixed
  harness and confirm Sprint 2 actually reaches `"Development"` before any `"Review, Retro &
  Release"` phase is reported.

## Resolution
Added `_prune_accepted_items_from_sprint_backlog(state)` (`agents/scrum_team/tools/github.py`) and
called it at both of `create_sprint_backlog_pr`'s existing success paths, guarded by the exact same
`sprint_backlog_pr_sprint != sprint_number` check those paths already use to decide "is this the
first time THIS sprint's own backlog PR has merged" (`github.py` - the "content already landed"
shortcut and the normal merge-success path). It drops every `sprint_backlog` item that already
reached `Accepted`, leaving anything not yet `Accepted` (genuine in-progress carry-over) untouched.

This is deliberately *not* a new refusal gate on `create_sprint_backlog_pr` - `plan_sprint_backlog_item`
is DevTeam-exclusive (`agent.py`), so a gate that told ProductOwner to call it directly would just be
a second instance of the exact unreachable-tool shape SPEC-ISSUE-0068 already closed for
`resolve_story_blocker`. Pruning stale `Accepted` items instead lets the *existing*, already-working
Sprint 1 mechanism do the job: once `sprint_backlog` is genuinely empty, `current_sprint_phase`
correctly reports `"Development"` (not `"Review, Retro & Release"`), DevTeam's own first attempt at
implementation correctly hits "hasn't been planned into this sprint yet - call
plan_sprint_backlog_item" exactly as it did for US-0001/US-0002 in Sprint 1, and the team naturally
self-serves from there - no new cross-role hand-off wording needed.

Tests added:
- `agents/scrum_team/tests/test_github.py` (`TestCreateSprintBacklogPr`):
  `test_prunes_a_prior_sprints_accepted_items_on_the_first_merge_this_sprint` (the direct regression
  test - replays `0.1.0-run74`'s exact `sprint_backlog` shape),
  `test_keeps_a_not_yet_accepted_item_that_legitimately_spans_the_sprint_boundary` (prune is
  selective, not a blanket clear), `test_does_not_reprune_on_a_second_merge_the_same_sprint` (mirrors
  the existing engagement-baseline idempotency test - a second call the same sprint must not discard
  something legitimately `Accepted` earlier this same sprint).
- `agents/scrum_team/tests/test_helpers.py` (`TestCurrentSprintPhase`):
  `test_development_once_backlog_published_with_an_empty_sprint_backlog` - confirms the
  `current_sprint_phase` side of the fix: a genuinely empty `sprint_backlog` with a matching
  `sprint_backlog_pr_sprint` reports `"Development"`, not `"Review, Retro & Release"`.

Full suite (`agents/scrum_team/tests`, via `docker compose run`, properly project-scoped per
`lib_docker.compose_project_args("test")`) passes: 1103 of 1108 tests, the same 5 pre-existing
failures (`test_agent.py`'s identity-fallback test and 4 in `test_steering_proposals.py`) present and
unrelated - confirmed via `git stash` A/B comparison when investigating SPEC-ISSUE-0069.
