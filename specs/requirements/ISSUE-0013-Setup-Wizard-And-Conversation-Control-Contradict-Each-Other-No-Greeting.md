# Issue

- Issue ID: ISSUE-0013
- Title: SETUP WIZARD and CONVERSATION CONTROL Contradict Each Other; No Proactive Greeting
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-28

## Overview
`ORCHESTRATOR_PROMPT`'s SETUP WIZARD section is headed "(run proactively until configured)" but its
own body says "If settings are missing from state and environment, ask user for: repo_url,
local_path, default_branch" - telling the model to ask rather than act, directly contradicting its
own header. Separately, OPERATING STYLE's CONVERSATION CONTROL bullet says "Do not start
implementation, concept work, or sprint planning unless specifically asked by the user after their
questions are answered" - which, combined with the above, gives the model an easy, prompt-consistent
excuse to treat an instruction like "let's start the sprint" as something to merely acknowledge or
ask more about, rather than act on immediately.

Separately, FIRST MESSAGE SUMMARY was the only instruction governing the orchestrator's very first
turn, and it required only a status recap ("inform the user that setup is required" if something is
missing) - nothing about greeting the user or proactively offering a concrete next step. A real user
reported exactly this symptom: after a working local setup and an explicit "let's start the sprint",
they observed no commits, no persisted sprint state, and no sense that the orchestrator was driving
the interaction forward - they expected it to "greet the user and offer guidance on what to do next."

## Acceptance Criteria
- SETUP WIZARD's header and body are reconciled: any user go-ahead to act is itself the instruction
  to run the wizard end-to-end (configure repo, seed repository, init state, etc.), not a cue to ask
  for settings that can reasonably be defaulted or inferred; the model only asks the user a question
  when a setting is genuinely missing from both state and environment.
- CONVERSATION CONTROL is scoped explicitly to genuine questions, not instructions to act, and
  explicitly does not override the mandatory-delegation rule (ISSUE-0012) once the user has actually
  asked the orchestrator to do something.
- FIRST MESSAGE SUMMARY requires: (1) a brief greeting, (2) the existing status summary, and (3) one
  concrete offered next action (either the orchestrator proceeding with the next setup step itself,
  or one specific clarifying question) - not a silent wait for the user to direct every step.

## Notes
- Where the gap lived: `agents/scrum_team/prompts.py`'s SETUP WIZARD (originally lines 75, 84-87),
  CONVERSATION CONTROL (originally line 159), and FIRST MESSAGE SUMMARY (originally lines 180-182).
- Complements ISSUE-0012: even once delegation itself is made mandatory, these contradictory/
  under-specified instructions would still let the model justify not acting on an explicit
  go-ahead, or leave the user without any sense of what to do next on the very first turn.

## Test Approach
- Prompt-behavior fix, verified by re-reading the edited sections against the acceptance criteria
  above (see ISSUE-0012's Test Approach for the same reasoning: not independently unit-testable the
  way a tool's return value is).

## Resolution
- `agents/scrum_team/prompts.py`, SETUP WIZARD: added an explicit paragraph reconciling the header
  with its body - a user go-ahead to act IS the instruction to run the wizard proactively; the
  existing "ask user for" bullet is now scoped to settings genuinely missing from both state and
  environment, not a general instruction to check in.
- CONVERSATION CONTROL: reworded to apply only to genuine questions, with an explicit cross-reference
  stating it does not apply once the user has asked the orchestrator to act, and that DELEGATION IS
  MANDATORY, NOT DESCRIPTIVE (ISSUE-0012) governs that case instead.
- FIRST MESSAGE SUMMARY: expanded into three numbered requirements - greeting, status summary, and
  one concrete offered next action (proceed with the next setup step, or ask one specific question).
