# Issue

- Issue ID: ISSUE-0012
- Title: Orchestrator Narrates Content Changes Instead of Delegating via transfer_to_agent
- Status: Done
- Priority: Must
- Owner: Scrum Team
- Last Updated: 2026-07-28

## Overview
`root_agent`'s own `tools=[...]` (`agents/scrum_team/agent.py`) intentionally holds no
content-authoring tool - no `write_file`, `upsert_prd`, `upsert_story`, `upsert_epic`, `start_sprint`,
`advance_story_stage`, or `git_push`; those all belong only to `product_owner`/`dev_team`/`architect`/
`scrum_master`. The only way the orchestrator can actually make any of those things happen is
`transfer_to_agent` to the owning role (ADK auto-injects this tool because `sub_agents` is
non-empty). However, `ORCHESTRATOR_PROMPT`'s ROUTING RULES only describe ownership as a table
("Priority/... -> Product Owner") - they never state that `transfer_to_agent` is mandatory before
producing content. The only place the prompt explicitly requires a `transfer_to_agent` call is once,
in SPRINT CLOSE SEQUENCE step 6, for the retrospective hand-off.

A real session transcript demonstrates the failure mode exactly: the user asked to "create specs and
a scaffolding to do list... roadmap, epics... behaviour driven given-when-then... architecture
decisions records", and the orchestrator's response was a single large JSON blob shaped like
`{"/specs/heinzelmann-product-prd.md": ["### Sprint Plan...` - fabricated content describing what a
spec would say, in a format `upsert_prd` (which enforces `specs/requirements/PRD-*.md` naming) never
produces. No `specs/` files were created, nothing was committed, and the `SYSTEM CONTEXT` block's
`Repository: Not configured (N/A)` never changed across the whole conversation. Given no delegation
tool call and no content-authoring tool of its own, narrating the content was the model's only
possible way to respond to the request at all.

## Acceptance Criteria
- `ORCHESTRATOR_PROMPT` states explicitly and unambiguously that the orchestrator has no tools
  capable of writing specs/stories/PRDs/ADRs/code/commits itself, and that any request to create or
  change one of those artifacts requires a `transfer_to_agent` call before responding with content.
- The instruction states plainly that describing content in a reply is not a substitute for a real
  tool call, and does not persist or commit anything.
- The instruction clarifies that a user request phrased as an instruction to act ("let's start the
  sprint", "let's create specs", "ok, do it") is itself sufficient grounds to delegate immediately.

## Notes
- Where the gap lived: `agents/scrum_team/prompts.py`'s ROUTING RULES section (originally lines
  108-118) described ownership only descriptively, unlike the "MANDATORY"/"HARD GUARDRAIL" language
  used elsewhere in the same file for rules that are actually code-enforced.
- Complements ISSUE-0011 (missing `start_sprint` tool) and ISSUE-0013 (setup wizard not actually
  proactive) - even with a real `start_sprint` tool and a reconciled setup wizard, the orchestrator
  still needed an explicit instruction that acting means delegating, not narrating.
- `docs/ARCHITECTURE.md`'s "Design Principle: Enforce Mandatory Process Mechanically, Not Just by
  Prompting" already documents this exact failure class as a known, recurring problem for this
  project - a rule stated only in a prompt is not reliably followed by a cheap model under budget
  pressure. This issue is squarely in the class that doc describes, though the actual fix here is
  necessarily prompt-level (there is no tool-layer equivalent of "you must delegate before replying"
  that ADK enforces directly on a root agent's own conversational output).

## Test Approach
- This is a prompt-behavior fix, not independently unit-testable the way a tool's return value is;
  verified by re-reading the edited `ORCHESTRATOR_PROMPT` text against the acceptance criteria above.
  Future eval runs (`agents/scrum_team/scripts/run_eval.py`) that exercise an explicit
  "let's start the sprint"-style user turn are the practical regression check that delegation (and
  therefore real tool calls/commits) actually happens instead of narrated text.

## Resolution
- Added a new "DELEGATION IS MANDATORY, NOT DESCRIPTIVE" block to `ORCHESTRATOR_PROMPT`
  (`agents/scrum_team/prompts.py`), placed right after ROUTING RULES and before SPRINT CLOSE
  SEQUENCE: states the orchestrator has none of the content-authoring/commit tools itself, requires
  `transfer_to_agent` before responding to any content-creation request, states that narrating
  content is never a substitute for a real tool call, and states that an instruction-phrased user
  request is itself sufficient grounds to delegate.
- ROUTING RULES' Scrum Master line now names `start_sprint` explicitly as something that routes to
  Scrum Master (see ISSUE-0011), so "let's start the sprint" has an unambiguous, named destination.
- Complements the CONVERSATION CONTROL reconciliation in ISSUE-0013, which removes the conflicting
  "don't act unless specifically asked" instruction that gave the model cover to narrate instead of
  delegate even after an explicit go-ahead.
