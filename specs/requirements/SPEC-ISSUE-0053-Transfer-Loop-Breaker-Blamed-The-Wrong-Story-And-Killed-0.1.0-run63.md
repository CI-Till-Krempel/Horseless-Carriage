# Issue

- Issue ID: SPEC-ISSUE-0053
- Title: Transfer-Loop Breaker Attached Its Blocker To The Wrong Story, Killing `0.1.0-run63` Over A Story Nobody Had Touched
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-14

## Overview
Reported (`0.1.0-run63`'s `report.md`): the run stopped after Sprint 1 of 5 with `stop_reason:
blocked_mechanically_detected`, naming **US-0002** ("Add a Task to a List") as the blocked story and
quoting: *"ScrumOrchestrator and DevTeam bounced transfer_to_agent 3x with no progress ... refusing
this transfer."* Taken at face value this reads as the team getting stuck trying to work US-0002. It
wasn't - US-0002 was never the subject of that conversation at all.

**Confirmed mechanism.** `transcript.md` event 118 shows DevTeam, in one pass, writing the entire MVP
(`app.py`, `templates/index.html`, `requirements.txt`, `README.md`, covering all of US-0001 through
US-0006) and pushing it to the feature branch/PR opened for **US-0001** - but never calling
`advance_story_stage(US-0001, "Implemented")`. Events 104-109 show an unrelated 6-hop
DevTeam<->ProductOwner transfer rotation get broken and attached to US-0001 (the message later quoted
back at event 128: *"Cannot advance 'US-0001' - it is BLOCKED (technical): DevTeam and ProductOwner
bounced transfer_to_agent 6x..."*) - correctly, since US-0001 was the only non-Accepted,
non-already-blocked story in the backlog at that moment.

Immediately after (events 116-120), ScrumOrchestrator and DevTeam bounce 3 more times while still
discussing US-0001's just-opened PR #1 (Architect is mid-review of that exact PR in the surrounding
events). The breaker fires again and calls the same attachment helper,
`_current_story_in_progress(product_backlog)` (`agents/scrum_team/tools/requirements.py:1314`):

```python
for item in product_backlog:
    if item.get("type", "User Story") == "Epic":
        continue
    if item.get("blocked"):
        continue
    if "Accepted" not in (item.get("stages_completed") or []):
        return item
```

Because US-0001 was *already* carrying the unresolved `blocked` flag from the first loop a few events
earlier, this filter skips it and returns the next backlog item - **US-0002** - which had no feature
branch, no PR, and no agent activity of any kind yet. The confirmed blocker recorded in
`manifest.json`'s final Sprint 1 snapshot proves this: `US-0002.blocked.raised_at_stage` is `"Ready"`
(US-0002 was still untouched), `blocking_interaction_id: 2`, and the `question` text is the verbatim
ScrumOrchestrator/DevTeam message - while the transcript has zero other hits for any BLOCKED message,
`advance_story_stage`, or `resolve_story_blocker` call ever naming US-0002.

Nobody in the run ever worked US-0002 again to notice or resolve this misattributed block - the team
kept making (slow) real progress on US-0001 (it reached `Implemented` -> `Reviewed` later that sprint,
then got stuck failing `Tested` 3x in an unrelated loop, see `SPEC-ISSUE-0001`-style test-fixture issues
already tracked). At the end of Sprint 1, `run_eval.py`'s `_sprint_needs_human_this_harness_cannot_provide`
check (`agents/scrum_team/scripts/run_eval.py:940`) found US-0002 still blocked with
`mechanically_detected`, and - correctly, *given that input* - concluded "the team already proved
itself unable to make progress through repeated attempts" and stopped the entire run. The premise was
false: the team never attempted US-0002 at all, let alone proved anything about it. A run that was
still inching forward on its actual story was killed over a misfiled blocker on an unrelated one.

`_current_story_in_progress` has one other caller, `_detect_unadvanced_implementation`
(`agents/scrum_team/agent.py:2173-2174`), which only uses it to *name* a story in a nudge message
(not to attach a blocker) - lower-risk, but it shares the same underlying bug: it too would name the
wrong story once the real one is already blocked. (`_detect_repeated_call_loop`, the sibling
repeated-call breaker, does **not** share this helper - it attaches directly via the stuck call's own
`title_or_id` argument, so it was never affected; an earlier draft of this issue said otherwise.)

## Acceptance Criteria
- When a mechanically-detected transfer loop fires while an earlier, still-unresolved block already
  exists on the story actually being worked, the new blocker attempt is directed at **that same
  story** (the one with an open feature branch - i.e. actually implicated by the surrounding tool-call
  context), not at whatever the next untouched backlog item happens to be.
- The attachment helper must never silently fall through to a story with zero recorded activity (no
  `start_feature_branch`, no PR) when a different story with an open, active feature branch is the one
  actually stuck - a story that has never been worked cannot be the subject of an "agents bounced with
  no progress" verdict.
- Since `raise_story_blocker` already refuses to double-block an already-`blocked` story, re-targeting
  the active story is sufficient and safe: it either attaches normally (not yet blocked) or fails over
  to the existing plain `record_blocking_interaction("stalled", ...)` path - never a fabricated
  story-level block with no evidence behind it.
- `run_eval.py`'s stop-the-run decision remains correct once its input is: it was not the bug here and
  needed no change, now that the blocker it reads is attached to the right story.

## Notes
- Gap lived in `_current_story_in_progress` (`agents/scrum_team/tools/requirements.py:1314`), called
  from `_detect_transfer_loop` (`agents/scrum_team/agent.py:2101-2114`) and
  `_detect_unadvanced_implementation` (`agents/scrum_team/agent.py:2173-2174`).
- This is distinct from the cross-sprint state-loss class (SPEC-ISSUE-0050/SPEC-ISSUE-0052) - no state was lost
  or regressed here; a *new* piece of state (the blocker) was written correctly in isolation but
  attributed to the wrong backlog entry given the already-blocked predecessor.
- `0.1.0-run63`'s broader Sprint 1 also surfaced a second, likely-related but separately-scoped
  problem worth a future look: US-0001 failing `Tested` 3 times in a row with "3 of 3 tests failed" -
  not investigated here since the run never got far enough to show whether it's a real test-fixture
  gap (as tracked by similar `SPEC-ISSUE-000x` retro items in other runs) or another symptom of this same
  misattribution class compounding.

## Test Approach
- `agents/scrum_team/tests/test_agent.py::TestLogToolInvocationCallbackBlocksSelfTransfer`: new case
  `test_transfer_loop_does_not_misattach_to_an_untouched_story_when_the_active_story_is_already_blocked`
  - a backlog with item 1 already `blocked` and recorded in `active_feature_branches`, item 2
  untouched (`Ready`, no branch); trips the transfer-loop breaker again and asserts item 2 never gets
  a `blocked` field, item 1's original blocker is unchanged, and a plain "stalled" interaction is
  recorded instead.
- Full `agents/scrum_team/tests` suite re-run (1010 passed, 1 skipped) to confirm no regression in the
  existing transfer-loop/rotation/unadvanced-implementation test classes.

## Resolution
- `agents/scrum_team/tools/requirements.py`: `_current_story_in_progress` now takes an optional
  `active_feature_branches` map and prefers the first non-Epic, non-Accepted story that has one -
  regardless of whether it's already `blocked` - before falling back to the original
  first-non-blocked-non-Accepted rule. A story with a real, open feature branch is the one implicated
  by anything happening right now, blocked or not.
- `agents/scrum_team/agent.py`: both call sites (`_detect_transfer_loop`,
  `_detect_unadvanced_implementation`) now pass `state.get("active_feature_branches")` through.
- `agents/scrum_team/tests/test_agent.py`: the new regression test above.
