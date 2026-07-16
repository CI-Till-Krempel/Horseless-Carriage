# User Story

- Story ID: US-0001
- Title: Capture Sub-Agent Turns in Conversation History
- Status: Done
- Priority: Must
- Owner: Scrum Master
- Last Updated: 2026-07-16

## As a Scrum Master, I want every sub-agent's request/response turns recorded (not just the Orchestrator's), so that the full multi-agent conversation is auditable after a sprint.

## Acceptance Criteria
- Given a sprint where the ProductOwner, ScrumMaster, DevTeam, QA, Architect, or QualityGuardian sub-agent runs a turn, when that turn completes, then it is appended to the shared transcript structure in state — not silently dropped.
- Given the existing `ScrumOrchestrator` capture path, when this change ships, then the Orchestrator's own turns continue to be captured exactly as before (no regression).
- Edge case: a sub-agent turn that itself invokes nested tool calls has all of its constituent events captured, not just the final text response.

## Notes
- Parent epic: EP-0001.
- Design/technical notes: `history_management_callback`/`history_management_after_callback` in `agents/scrum_team/agent.py` currently hard-return via `if callback_context.agent_name != "ScrumOrchestrator": return`. This needs to become inclusive (tag entries with `agent_name`) rather than exclusive.

## Test Approach
- Unit test simulating a callback invocation with `agent_name` set to each non-Orchestrator role, asserting the entry is appended.
- Regression test confirming Orchestrator-turn capture behavior is unchanged.
