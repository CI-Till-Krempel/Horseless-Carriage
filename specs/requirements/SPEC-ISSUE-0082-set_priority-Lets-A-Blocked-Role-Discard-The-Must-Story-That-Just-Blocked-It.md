# Issue

- Issue ID: SPEC-ISSUE-0082
- Title: `set_priority` Lets Product Owner Discard The Exact Must-Priority Story That Just Blocked A Lower-Priority Item, With Zero Justification Or Audit Trail
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-11-02

## Overview
Investigated `0.1.0-run80` (`gh run view 41416190961`, commit `cac04b46d984e0ba2e32c9041d4edb2d62d27a58` -
already includes SPEC-ISSUE-0076/0078/0081's fixes). `report.md` names, as Top Problems #1/#2: *"Core
product requirements remain incomplete"* (US-0004/US-0005/US-0006 never implemented) and *"Excessive
focus on internal process compliance and meta-issues"* - on the surface, the same livelock shape
SPEC-ISSUE-0081 already fixed.

**Confirmed: SPEC-ISSUE-0076/0078/0081 are all working exactly as designed in this run - this is a
different, new mechanism.** `_file_retro_items_as_issues`'s recurrence check (SPEC-ISSUE-0081) correctly
caught that SPEC-ISSUE-0003 ("test isolation... fixtures properly reset") was a near-duplicate of
SPEC-ISSUE-0002 and correctly left it out of `_move_item_to_front_of_remaining_work` - US-0004/US-0005/
US-0006 (filed earlier, still `Must`) stayed ahead of it in `product_backlog` order exactly as intended.
When DevTeam tried to start work on SPEC-ISSUE-0003 anyway, the one-story-at-a-time ordering gate
correctly refused it: *"Cannot start work on 'SPEC-ISSUE-0003' - the higher-priority story 'US-0006'
must reach Accepted first. Development happens one story at a time, top to bottom, in backlog priority
order."* (`transcript.md:14672`, repeated identically at `12443`/`14552`).

**The actual bug: Product Owner's response to that correct rejection.** Rather than implementing
US-0004/US-0005/US-0006 (all three had been sitting at `['Draft', 'Ready']` - groomed, never started -
since Sprint 1, per `manifest.json`'s own per-sprint `product_backlog` snapshots), ProductOwner called
`set_priority(title_or_id, "Won't")` on US-0006, then US-0005, then US-0004 in immediate succession
(`transcript.md:14696-14836`), narrating e.g. *"I am overriding set_priority to set US-0006 priority to
Won't for this sprint scope."* (`transcript.md:14724`) and *"I am overriding set_priority to set
US-0004 priority to Won't."* (`transcript.md:14836`). Each call first hit the SPEC-ISSUE-0057 phase gate
(*"set_priority does not belong in the current phase ('Development')... if this genuinely needs to
happen now, call set_priority again with the exact same arguments - that immediate retry will be let
through and logged as an explicit override"* - `transcript.md:14705-14712`) - ProductOwner immediately
retried identically each time, which the phase gate is deliberately designed to allow. `manifest.json`
confirms the result: all three stories are `Must`/`['Draft', 'Ready']` through Sprint 3's snapshot, then
`Won't`/`['Draft', 'Ready']` from Sprint 4 onward - never once attempted past `Ready`, discarded instead
of implemented, the moment they became inconvenient blockers.

**Why this is a distinct mechanism, not a SPEC-ISSUE-0081 regression.** SPEC-ISSUE-0081 closed the path
where a *process finding about* unfinished Must stories could queue-jump ahead of them. This run shows a
second, independent path to the identical outcome (real PRD scope silently dropped): a role facing a
correct ordering-gate rejection can just reprioritize the blocking story away instead of resolving it.
`advance_story_stage` already has a precedent for exactly this failure mode - its own ordering-gate
rejection message, when the blocker is an auto-filed Issue, explicitly warns *"rather than reaching for
`set_priority(...)` to dodge it... Only reprioritize away from 'Must' if, on genuine reflection, it
turns out this finding never actually warranted blocking priority - not because resolving it is
inconvenient right now"* (`transcript.md:3931`, `agents/scrum_team/tools/requirements.py:1706-1712`).
That warning covers only the Issue case (text present only when `preceding.get("type") == "Issue"`) and
is advisory prose in a rejection message a role can simply not trigger by calling `set_priority` instead
of retrying `start_feature_branch`/`advance_story_stage` - it does nothing to prevent the call itself.
`set_priority` (`agents/scrum_team/tools/requirements.py:943-973`) has no guard at all: no justification
parameter, nothing written to `decision_log` (contrast `declare_backlog_scope_complete`,
`implemented_via_earlier_work`, `content_loss_acknowledgment` - every other "honest escape hatch" in this
codebase requires a real, specific, logged reason), and no check for whether the target item is the
*exact* story a gate rejection just named as the thing blocking other work this same sprint.

## Acceptance Criteria
- Lowering a `Must`-priority **story** (not an Issue - Issues already have SPEC-ISSUE-0081's recurrence
  handling) to `Could`/`Won't` via `set_priority` must require a real, specific `justification` argument,
  logged to `decision_log` (same pattern as `implemented_via_earlier_work`/`content_loss_acknowledgment`/
  `declare_backlog_scope_complete`) - a blank or low-quality placeholder is rejected the same way those
  are (`is_low_quality_retro_text`).
- `set_priority` must refuse outright (not just warn) to demote a story's priority away from `Must` when
  that exact story was named as the blocking precedent in an `advance_story_stage`/`start_feature_branch`
  ordering-gate rejection earlier in the *same sprint* - the one scenario this run shows is actually
  happening, not a hypothetical. A regression test replaying `0.1.0-run80`'s shape (an ordering-gate
  rejection naming `US-0006` as blocking, immediately followed by `set_priority('US-0006', "Won't")`)
  must assert the `set_priority` call is rejected, not silently accepted.
- A story reprioritized away from `Must` with a genuine, logged justification (the legitimate case -
  "on genuine reflection this never warranted blocking priority") must still succeed normally, unaffected
  by the above - this is additive scope protection, not a general ban on reprioritization.

## Notes
- Distinct from SPEC-ISSUE-0076/0078/0081: all three are confirmed working correctly in this run
  (`transcript.md:12443`, `14552`, `14672` all show the ordering gate firing exactly as those fixes
  intend) - this issue is about the *response* to a correct rejection, not the rejection mechanism
  itself.
- The existing "don't reach for `set_priority` to dodge it" warning in `advance_story_stage`
  (`requirements.py:1706-1712`) already names this exact failure mode for the Issue case - this issue
  extends the same judgment to real stories and, more importantly, makes it a mechanical refusal instead
  of prose a cheap model can route around by calling a different tool.
- `run80`'s target repo (`CI-Till-Krempel/horseless-carriage-eval-todo-app`, eval branch
  `eval/0.1.0-run80/*`) is an evidence artifact of this run, not a real tracked issue in this codebase -
  referenced here only as the data this investigation is based on.
- Two open "Steering proposal: DevTeam-identity.md" PRs (#704, #709) in the eval repo were noticed but not
  investigated further - out of scope for this issue, worth a separate look if they recur.
- **Known gap, deliberately not closed here**: `upsert_backlog_item`/`upsert_story` (and the other
  `upsert_*` wrappers) merge a `{"priority": ...}` field directly into the backlog item dict, bypassing
  `set_priority` (and therefore this fix) entirely - `requirements.py`'s own "SPEC-ISSUE-0007" comment right
  above that merge explicitly notes "Priority needs no equivalent sync" for the roadmap-sync concern it was
  written for, but that reasoning doesn't extend to this new guard. Not fixed here to keep this change
  scoped to the one tool `0.1.0-run80` actually showed being used this way - worth a follow-up issue if a
  future run shows a role route around `set_priority` via `upsert_story` instead.

## Test Approach
- `agents/scrum_team/tests/test_requirements.py` (new `TestSetPriorityGuardsAgainstDiscardingABlocker` or
  similar): a story reprioritized from `Must` with no `justification` (or a low-quality one) is rejected;
  a real justification succeeds and is logged to `decision_log`.
- Same file: `set_priority` called on a story that was named as the blocker in a same-sprint
  `advance_story_stage`/`start_feature_branch` rejection is refused outright, regardless of
  `justification` - replaying `0.1.0-run80`'s exact sequence (reject naming `US-0006`, then
  `set_priority('US-0006', "Won't")`) must now fail.
- Full `agents/scrum_team/tests` suite must still pass with no regressions to legitimate reprioritization
  (e.g. existing `TestSetPriorityValidation`, `TestPriorityAffectsBacklogOrdering`).

## Resolution
- `agents/scrum_team/tools/base.py` (`_record_priority_block_precedent`): new helper, mirroring
  `_record_touched_file`/`_record_source_content_change`'s existing pattern - records `{story_id:
  sprint_number}` into `ScrumState.priority_block_precedents` whenever a story is named as the blocker in
  an ordering-gate rejection.
- `agents/scrum_team/tools/requirements.py` (`advance_story_stage`'s one-story-at-a-time gate) and
  `agents/scrum_team/tools/github.py` (`start_feature_branch`'s equivalent gate): both now call
  `_record_priority_block_precedent(preceding_ref, ...)` right when they name a story as the blocker -
  the exact two call sites `0.1.0-run80`'s transcript shows firing this rejection.
- `agents/scrum_team/tools/requirements.py` (`set_priority`): new `justification` parameter. Demoting a
  real story (`type != "Issue"`) away from `Must` now requires a real, non-placeholder `justification`
  (`is_low_quality_retro_text`), logged to `decision_log` - same pattern as
  `implemented_via_earlier_work`/`content_loss_acknowledgment`. If the target story's id is recorded in
  `priority_block_precedents` for the *current* sprint, the call is refused outright regardless of
  justification - replays as a hard stop, not a warning, exactly where `0.1.0-run80` showed it actually
  happen. Promoting priority, demoting a non-`Must` priority, and demoting an `Issue` (SPEC-ISSUE-0081's
  own territory) are all unaffected.
- `agents/scrum_team/tools/requirements.py` (`plan_backlog_item`): new `priority_justification` parameter,
  forwarded to `set_priority`, so the legitimate "this genuinely never warranted Must" case Product Owner
  needs remains reachable through this tool too.
- New tests: `test_requirements.py`'s `TestSetPriorityGuardsAgainstDiscardingABlocker` (justification
  required/rejected-if-low-quality/logged-to-decision_log; outright refusal when the story is a recorded
  same-sprint blocking precedent; unaffected: promotions, non-Must demotions, Issue demotions, a different
  sprint's stale precedent) and a `plan_backlog_item` passthrough test. Full `agents/scrum_team/tests`
  suite passes with no regressions.
