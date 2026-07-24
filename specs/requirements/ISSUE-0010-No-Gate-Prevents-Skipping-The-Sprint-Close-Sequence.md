# Issue

- Issue ID: ISSUE-0010
- Title: No Gate Prevents Skipping the Sprint Close Sequence
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-24

## Overview
`ORCHESTRATOR_PROMPT`'s SPRINT CLOSE SEQUENCE (lines 103-123) spells out, in unusually emphatic
detail, that step 6 (Scrum Master retrospective) must happen "**Do this every sprint,
unconditionally** - do not skip straight to step 7," and step 7 says "Do NOT end the sprint, and do
NOT just keep transferring between yourself and Scrum Master, until Product Owner has actually made
both of those two tool calls successfully - check session state (`sprint_report` non-empty) rather
than assuming a hand-off implies completion." The level of detail here (repeated warnings against
two specific observed failure modes: skipping step 6, and assuming a hand-off means completion) is
itself a signal that this was previously a real, observed problem being patched with prompt text
rather than code. `create_sprint_report`'s `retro_baseline` gate does mechanically stop a report
from being generated without a fresh retro/impediment entry - a real, code-level improvement - but
nothing stops the *orchestrator* from simply never transferring to Scrum Master at all and instead
ending the interaction, nor from starting a *new* sprint's work (planning/implementation) while the
previous sprint's `create_release_pr` was never actually called. The sequence's ordering is entirely
prompt-obeyed, not code-enforced.

## Acceptance Criteria
- A code-level check (e.g. surfaced through `init_scrum_state` or a dedicated `start_sprint`/
  `plan_sprint_backlog_item` guard) refuses to add new items to `sprint_backlog` for a new sprint
  while the previous sprint has planned stories that never reached Accepted AND no `create_release_pr`
  call succeeded since - mirroring the "must be NEW, not just non-empty" reasoning already used for
  `retro_baseline`.
- Rejection message names the specific prior-sprint step that was skipped (retrospective vs. release
  PR) so the caller has a concrete next action instead of a vague "process violated" message.
- A test exists: starting new sprint-backlog work with the prior sprint's release PR never created
  is rejected; after `create_release_pr` succeeds, new sprint work is accepted.

## Notes
- Where the gap currently lives: no file enforces sprint-boundary sequencing today - the SPRINT
  CLOSE SEQUENCE in `agents/scrum_team/prompts.py` (`ORCHESTRATOR_PROMPT`, lines 103-123) is the only
  place this rule exists.
- Complements (doesn't duplicate) the existing `retro_baseline` gate in `create_sprint_report`
  (`agents/scrum_team/tools/budget.py`): that gate stops a *report* from being generated without a
  fresh retro signal; this issue is about stopping the *next sprint* from starting without the
  *previous* sprint's close sequence (retro AND release PR) having actually completed.
- Traces back to `agents/scrum_team/prompts.py` lines 103-123 (`ORCHESTRATOR_PROMPT`, SPRINT CLOSE
  SEQUENCE).

## Test Approach
- Unit test on the new guard function: simulate a prior sprint with stories short of Accepted and no
  release PR recorded, assert the next-sprint-start call is rejected; simulate a completed prior
  sprint (release PR recorded) and assert it's accepted.
