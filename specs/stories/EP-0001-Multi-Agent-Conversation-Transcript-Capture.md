# Epic

- Epic ID: EP-0001
- Title: Multi-Agent Conversation Transcript Capture
- Status: Done
- Priority: Must
- Owner: Scrum Master
- Last Updated: 2026-07-16

## Overview
Today `agents/scrum_team/agent.py`'s history-management callbacks only persist the `ScrumOrchestrator`'s own turns (they hard-return for any other `agent_name`), so the actual sub-agent-to-sub-agent dialogue — the bulk of what the multi-agent system does during a sprint — is never captured as a durable, auditable transcript. This epic makes the full multi-agent conversation a first-class, persisted sprint artifact.

## User Stories / Features
- US-0001 — Capture Sub-Agent Turns in Conversation History
- US-0002 — Persist Full Transcript to State Repo
- US-0003 — Expose Transcript in Sprint Report
- US-0004 — Trim Transcript for Token Budget

## Acceptance Criteria
- Every sub-agent's request/response turns are recorded, not just the Orchestrator's.
- The full transcript survives a container/session restart (persisted to the state repo, not just in-memory).
- The sprint report links to or excerpts the transcript so reviewers can trace which agent made which decision.
- Long-running sprints don't blow the token budget when transcript history is replayed back into context.

## Notes
- Primary touch point: `history_management_callback` / `history_management_after_callback` in `agents/scrum_team/agent.py`, currently gated by `if callback_context.agent_name != "ScrumOrchestrator": return`.
- Depends on `save_state_to_repo` (`agents/scrum_team/tools/scrum.py`) for persistence.
- Risk: naively capturing every sub-agent turn could itself blow the token/USD budget tracked via `tools/budget.py` — see US-0004.

## Roadmap
- Targeted for v0.1 — Trust & Integrity Fixes (see `specs/ROADMAP.md`).
