# Issue

- Issue ID: ISSUE-0009
- Title: Retro Gate Checks Presence Only, Not Specificity
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-24

## Overview
`SM_PROMPT`'s RETROSPECTIVE REASONING section (MANDATORY, lines 247-266) is explicit that a retro
action must be "not generic (\"communicate better\") but tied to what actually happened this sprint"
and that impediments must be analyzed "concretely." `create_sprint_report`
(`agents/scrum_team/tools/budget.py`) already mechanically enforces that *something new* was logged
via `add_retro_action`/`add_impediment` since the last report (the `retro_baseline` gate, added to
close a real observed gap where Scrum Master went un-invoked for 5 sprints straight). That gate is a
real improvement, but it only checks *count*, not *content quality* - `add_retro_action("stuff",
"SM", "better")` satisfies it exactly as well as a genuine, specific action item tied to real
evidence. The prompt's explicit "not generic" instruction has no code backstop at all; the model can
satisfy the mechanical gate with a placeholder and move on.

## Acceptance Criteria
- `add_retro_action` rejects (or `create_sprint_report`'s gate additionally checks) entries where
  `action`/`owner`/`success_metric` are blank, a template placeholder, or under some minimum useful
  length - the same class of mechanical backstop `_story_readiness_issues` already applies to story
  content.
- Rejection message names which field is too thin and points back at
  `spec-templates/DOD.md`/`SM_PROMPT`'s own example ("Architect wasn't consulted before 2 stories
  were marked Ready...") as the bar to clear.
- A test exists: `add_retro_action("stuff", "SM", "better")` (or similarly generic) is rejected;
  a real action with all three fields meaningfully filled in succeeds.

## Notes
- Where the gap currently lives: `add_retro_action`/`add_impediment`
  (`agents/scrum_team/tools/scrum.py`) only `.strip()` the input fields, with no minimum-content
  check; `create_sprint_report`'s `retro_baseline` gate (`agents/scrum_team/tools/budget.py`) only
  compares counts.
- Intentionally a lighter-touch check than a "genericness" classifier (out of scope for a mechanical
  gate) - a blank/placeholder/too-short check is the same class of backstop already proven useful for
  story content in `_story_readiness_issues`.
- Traces back to `agents/scrum_team/prompts.py` lines 247-266 (`SM_PROMPT`, RETROSPECTIVE REASONING).

## Test Approach
- Unit test alongside the existing `test_budget.py` retro-gate tests: seed a generic/blank retro
  action and assert rejection; seed a properly filled-in one and assert it's accepted.
