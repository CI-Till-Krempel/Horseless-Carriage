# Issue

- Issue ID: ISSUE-0011
- Title: No Tool Ever Sets sprint_goal, So a Sprint Never Actually Starts
- Status: Done
- Priority: Must
- Owner: Scrum Team
- Last Updated: 2026-07-28

## Overview
`ORCHESTRATOR_PROMPT`'s ITERATION MODE says "The team works in iterations" and lists `sprint_goal`
among the artifacts the team maintains (CORE GOAL). In practice, no tool anywhere in the codebase
ever sets `sprint_goal` to a real value: `init_scrum_state` only ever does
`s.setdefault("sprint_goal", "")` (`agents/scrum_team/tools/scrum.py`), and a full-codebase grep for
`sprint_goal` outside `scrum.py`'s persisted-key list turns up nothing that writes it. A real session
transcript showed exactly this: the user said "Ok, lets start the sprint then" and later "Lets start
a sprint that focusses on conceptional work... let the PO and Architect review the specs", and the
orchestrator responded with an elaborate, well-structured plan every time - but the injected
`SYSTEM CONTEXT` block showed `Sprint Goal: Not yet defined` and `Sprint Backlog: 0/0 items
completed` unchanged across every single turn. No amount of prompting the orchestrator to "start a
sprint" can fix this: the capability to record a sprint goal simply didn't exist.

## Acceptance Criteria
- A tool exists (`start_sprint(goal)`) that actually sets `sprint_goal` in session state and persists
  it via `save_state_to_repo`.
- It rejects a blank, generic/placeholder, or too-short goal (mirroring the existing
  `is_low_quality_retro_text` guard already used for retro/impediment text).
- It refuses to start a new sprint while the previous sprint's close sequence is left unfinished
  (reusing `new_sprint_item_blocked`, see ISSUE-0010) - starting a new sprint goal is "new sprint
  work" for that gate's purposes, same as a new `sprint_backlog` item.
- The tool is wired into an agent's tool list (Scrum Master, who facilitates Sprint Planning per
  ROUTING RULES) and referenced in that role's prompt as the mechanical kickoff of a sprint.
- A test exists: blank/placeholder goal is rejected; a real goal is accepted and persisted; starting
  is rejected while the previous sprint's release PR is still pending, and accepted again afterward.

## Notes
- Where the gap lived: `agents/scrum_team/tools/scrum.py`'s `REPO_STATE_KEYS`/`init_scrum_state`
  (lines 20-50, 56-177 before this fix) - `sprint_goal` was tracked as state but never had a setter.
- Complements ISSUE-0012 (delegation isn't mandatory) and ISSUE-0013 (setup isn't actually
  proactive): even with both of those fixed, a sprint still could not "start" without this tool
  existing at all.
- Traces back to `agents/scrum_team/prompts.py`'s ITERATION MODE section and the CORE GOAL artifact
  list, both of which named `sprint_goal` as something the team maintains without any tool able to
  set it.

## Test Approach
- Unit tests on the new tool directly (`agents/scrum_team/tests/test_scrum.py`): sets a real goal
  and persists it; rejects blank/placeholder text without mutating state; rejects while
  `sprint_report_pending_release` is set with unfinished stories, and succeeds once that clears -
  following the existing pattern in `test_plan_sprint_backlog_item_rejects_new_work_while_release_pending`.

## Resolution
- Added `start_sprint(goal, tool_context)` to `agents/scrum_team/tools/scrum.py`: validates the goal
  via `is_low_quality_retro_text`, checks `new_sprint_item_blocked`, sets `sprint_goal`, and persists
  via `save_state_to_repo`.
- Exported from `agents/scrum_team/tools/__init__.py` and wired into `ScrumMaster`'s tool list in
  `agents/scrum_team/agent.py`.
- `agents/scrum_team/prompts.py`: `SM_PROMPT`'s WORKFLOW section now documents `start_sprint` as the
  mechanical kickoff of Sprint Planning and adds it to SM's "Use tools:" list; `ORCHESTRATOR_PROMPT`'s
  ITERATION MODE and ROUTING RULES now name `start_sprint`/Scrum Master explicitly, so "let's start
  the sprint" routes to a real tool call instead of narrated text (see ISSUE-0012).
- Tests: `test_scrum.py::test_start_sprint_sets_sprint_goal`,
  `test_start_sprint_rejects_blank_or_placeholder_goal`,
  `test_start_sprint_rejects_new_work_while_release_pending`.
