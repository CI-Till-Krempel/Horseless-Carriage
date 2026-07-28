# Issue

- Issue ID: ISSUE-0016
- Title: Sprint Stays Interactive Instead of Autonomous at Stakeholder and CEO Levels
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-28

## Overview
Reported (GitHub issue #46): running at `INTERACTION_LEVEL=Stakeholder`, the team's dialog style
stayed conversational/turn-by-turn instead of running continuously, only breaking at the points a
Stakeholder actually needs to be involved (reviewing/approving the sprint goal and backlog, giving
feedback on implemented features, and discussing new business needs with PO). The expectation - the
cross-agent pipeline running automatically end-to-end between those two points - matches how
`INTERACTION_LEVEL` is documented to work for Stakeholder (`docs/INTERACTION-LEVELS.md`: "A business
stakeholder, not embedded in day-to-day PO work... Business needs, release order, approves new
features, gives sprint-review feedback").

The root cause: `ORCHESTRATOR_PROMPT`'s `INTERACTION-LEVEL DETAIL` section (OPERATING STYLE) only
ever governed the *content*/tone of a message sent to the human (task-level detail at Product,
business framing at Stakeholder, one-line summaries at CEO) - nothing governed *how often* the
orchestrator should stop and produce a user-facing reply at all. `docs/INTERACTION-LEVELS.md`'s own
"What's still prompt-only, deliberately" section already anticipated this exact class of gap:
"Getting a PO agent to actually ask task-level questions... or to keep a CEO-level response to one or
two sentences... is conversational judgment... If a real run surfaces the team ignoring this
distinction as a repeatable problem, file it as an Issue" - this is that real run.

## Acceptance Criteria
- `ORCHESTRATOR_PROMPT` explicitly distinguishes "what to say" (existing INTERACTION-LEVEL DETAIL)
  from "how often to say anything at all" (new).
- At Product: turn-by-turn conversation remains explicitly correct - this human is the embedded
  Product Owner and needs to answer genuine task-level questions as they arise.
- At Stakeholder/CEO: the instruction requires driving the full story pipeline (Ready through
  Accepted, then the sprint close sequence) via chained `transfer_to_agent` hand-offs without an
  intervening user-facing reply after each individual hand-off, addressing the human only at
  mechanical approval gates, a genuine business/budget decision only they can make, or the final
  sprint report.
- At EVAL: unaffected - already fully autonomous by design.
- `docs/INTERACTION-LEVELS.md`'s "What's still prompt-only, deliberately" section is updated to
  reference this issue and the resulting prompt section, per its own stated convention.

## Notes
- Where the gap lived: `agents/scrum_team/prompts.py`'s `OPERATING STYLE` -> `INTERACTION-LEVEL
  DETAIL` section governed message content only; nothing governed message *frequency*/turn-taking.
- This is mechanically possible without any ADK-level change: `transfer_to_agent` hands off control
  within the same invocation, and a sub-agent that itself calls tools/transfers again (rather than
  emitting a plain-text reply) never surfaces anything to the human in between - the orchestrator
  and its sub-agents already have everything needed to chain silently; nothing was instructing them
  to actually do so at Stakeholder/CEO levels.
- Deliberately prompt-only, per `docs/INTERACTION-LEVELS.md`'s own stated pattern - "did the team
  stop and chat unnecessarily" isn't something a mechanical gate can check the way `check_build()`
  can check "did the build pass".

## Test Approach
- Prompt-behavior fix, not independently unit-testable via `pytest` the same way a tool's return
  value is (consistent with ISSUE-0012/0013's Test Approach) - verified by re-reading the edited
  `ORCHESTRATOR_PROMPT` section against the acceptance criteria above. The practical regression
  check is a future eval run (`agents/scrum_team/scripts/run_eval.py`) or real session at
  `INTERACTION_LEVEL=Stakeholder` observing the team run the pipeline continuously between approval
  points instead of stopping after every hand-off.

## Resolution
- Added a new "AUTONOMY BY INTERACTION LEVEL" section to `ORCHESTRATOR_PROMPT`
  (`agents/scrum_team/prompts.py`), placed after ITERATION MODE: explicitly separates "what to say"
  (existing INTERACTION-LEVEL DETAIL) from "how often to say it", with per-level behavior (Product:
  turn-by-turn is correct; Stakeholder/CEO: chain `transfer_to_agent` through the full pipeline
  without an intervening reply, surfacing only at approval gates/genuine business-or-budget
  decisions/the final report; EVAL: already fully autonomous).
- Cross-referenced from the existing INTERACTION-LEVEL DETAIL bullet, so the two concerns don't read
  as duplicates of each other.
- Updated `docs/INTERACTION-LEVELS.md`'s "What's still prompt-only, deliberately" section to
  reference this issue and the new prompt section, per the doc's own stated convention for exactly
  this situation.
