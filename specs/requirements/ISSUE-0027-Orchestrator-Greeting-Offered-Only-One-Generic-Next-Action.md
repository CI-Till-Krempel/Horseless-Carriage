# Issue

- Issue ID: ISSUE-0027
- Title: Orchestrator's First-Message Greeting Offered Only One Generic Next Action
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #58): the Orchestrator's first message on connecting should greet the user,
run a quick self-check, and proactively offer 2-5 concrete next-action options informed by the
current repo/scrum state (implementation status, requirements, sprint reports, impediments, retro
actions) - not wait passively or offer only a generic acknowledgment. The issue lists typical options
(start/resume a sprint, work on the product vision, improve the roadmap, plan version increments,
discuss an impediment, implement a retro action, refine stories, discuss the sprint backlog, do a
topic-specific retro) and expects the Orchestrator to fork to the right subagent(s) once one is
picked, using the existing toolchain/routing.

A prior fix (ISSUE-0013) already addressed the more basic version of this gap - the Orchestrator used
to not proactively greet at all - by adding a FIRST MESSAGE SUMMARY requirement: greeting + status
recap + **exactly ONE** concrete offered action. That was a deliberate choice at the time (avoid
overwhelming a Product-level user with a menu every turn), but it directly conflicts with this
issue's ask for a short menu of options - and the status recap it was built on
(`sprint_status_injection_callback`, `agents/scrum_team/agent.py`) only ever surfaced sprint
goal/backlog-completion-count/budget/repo/interaction-level - never product vision, sprint-report
status, impediments, retro actions, or which stories are actually ready for another role to pick up,
so there was no state signal to build a richer menu from even if the prompt asked for one.

## Acceptance Criteria
- `sprint_status_injection_callback` injects the additional state signals the issue asks for:
  whether a product vision is set, whether a sprint report already exists for the current sprint,
  open-impediment count (+ most recent), open-retro-action count (+ most recent), and how many
  backlog stories are one pipeline stage short of the next role picking them up (not just the
  "Ready" stage specifically - the issue's own example - but any Ready→Implemented→Reviewed→Tested→
  Accepted transition).
- `ORCHESTRATOR_PROMPT`'s FIRST MESSAGE SUMMARY is updated to supersede ISSUE-0013's "end with ONE
  concrete action" with: once setup is complete, end with 2-5 concrete, state-informed options drawn
  from the issue's own list, prioritized by what the new signals above actually indicate is true right
  now (e.g. don't offer "resume an interrupted sprint" when there's no sprint goal at all; do offer
  "discuss impediment" first when one is open) - never padding the menu with irrelevant options just
  to hit a count, and never more than 5 at once.
- Setup-incomplete sessions are unaffected: still one concrete action (run the missing step, or ask
  the one specific blocking question) - a menu of "next sprint actions" makes no sense before the
  team is even configured.
- Whichever option the user picks is itself the instruction to act on it, per the already-existing
  DELEGATION IS MANDATORY / ROUTING RULES sections - no new mechanism needed there, just an explicit
  cross-reference so the menu doesn't read as a dead end.

## Notes
- This is primarily a prompt-engineering change (`ORCHESTRATOR_PROMPT`), backed by a small, testable
  code extension (the new state signals) - consistent with how ISSUE-0013 itself was implemented and
  verified (prompt content re-read against acceptance criteria, since there's no way to unit-test an
  LLM's actual conversational output), plus real unit tests for the mechanical parts.
- **Not wired in**: `blocking_interactions` (GH issue #53 / ISSUE-0025) as an additional signal - that
  field doesn't exist on `ScrumState` in this branch's base (`main`) at the time of this change (it's
  part of a separate, not-yet-merged PR). Once that merges, surfacing open blocking interactions in
  this same status summary would be a natural, small follow-up - open blocking interactions are
  arguably the single highest-priority thing to mention first in the greeting.
- Impediments/retro actions don't currently have any mechanism to ever leave `status: "open"` (no
  tool marks one resolved) - counting is still meaningful for "is there something to discuss" as a
  greeting signal, but a real resolution-tracking mechanism is out of scope for this change.
- Where the gap lived: `agents/scrum_team/agent.py`'s `sprint_status_injection_callback` (missing
  signals) and `agents/scrum_team/prompts.py`'s FIRST MESSAGE SUMMARY (explicitly capped at one
  action, superseded here).

## Test Approach
- `agents/scrum_team/tests/test_agent.py::TestAgent::test_sprint_status_injection_surfaces_process_signals` -
  product vision, sprint-report status, open impediments (+ most recent), retro actions (+ most
  recent), and ready-for-next-stage count are all present in the injected system message.
- `test_sprint_status_injection_defaults_when_nothing_set_yet` - all-defaults case reports "Not yet
  defined"/0/"not yet created" cleanly.
- `test_resolved_impediments_and_retro_actions_are_not_counted_as_open` - a `status` other than
  `"open"` doesn't count (forward-compatible with a future resolution mechanism, even though nothing
  sets one today).
- `TestStoriesReadyForNextStageCount` (new class) - `_stories_ready_for_next_stage_count`: Ready-but-
  not-Implemented counts, Accepted (fully done) doesn't, counts across both backlog lists, a story
  with no `stages_completed` at all doesn't count.
- Pre-existing `test_sprint_status_injection_callback` passes unmodified (its exact substring
  assertions are still all present in the extended output).
- Run via `docker compose --env-file .env.test run --rm --entrypoint "" -e PYTHONPATH=/app agent
  pytest --cov=agents agents/scrum_team/tests` (per `docs/TESTING.md`): 190 passed, no regressions.
- `pytest tests/` (host-side suite, unaffected by this change): 215 passed.

## Resolution
- `agents/scrum_team/agent.py`: `_stories_ready_for_next_stage_count()` (new helper), and
  `sprint_status_injection_callback` extended with the five new signal lines described above.
- `agents/scrum_team/prompts.py`: FIRST MESSAGE SUMMARY rewritten - greeting/status-recap requirements
  kept, "end with ONE action" replaced with a prioritized 2-5-option menu drawn from the issue's own
  list, explicit cross-reference to DELEGATION IS MANDATORY/ROUTING RULES for what happens once one
  is picked.
- `MANUAL.md` § "4. Running a sprint": short paragraph describing the greeting/menu behavior for
  end users.
