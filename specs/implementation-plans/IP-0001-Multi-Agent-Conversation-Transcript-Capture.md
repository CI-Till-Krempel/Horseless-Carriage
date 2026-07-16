# Implementation Plan

- Plan ID: IP-0001
- Title: Multi-Agent Conversation Transcript Capture
- Epic: EP-0001
- Status: Draft
- Owner: Scrum Master
- Last Updated: 2026-07-16

## 1. Objective
Make the full multi-agent conversation — not just the `ScrumOrchestrator`'s own turns — a durable, auditable sprint artifact.

## 2. Approach
Change `history_management_callback`/`history_management_after_callback` in `agents/scrum_team/agent.py` from an exclusive gate (`if agent_name != "ScrumOrchestrator": return`) to an inclusive one: every sub-agent's turn is tagged with its `agent_name` and appended to a shared `transcript` list in `ScrumState`. Persist that list through the existing `save_state_to_repo` path by adding `transcript` to `REPO_STATE_KEYS` (`agents/scrum_team/tools/scrum.py`). Apply US-0004's trimming before re-injecting history into context, so growth is bounded before it ever threatens the token/USD budget enforced by `tools/budget.py`.

## 3. Affected Components / Files
- `agents/scrum_team/agent.py` — `history_management_callback`, `history_management_after_callback`.
- `agents/scrum_team/state.py` — new `transcript: List[Dict]` field on `ScrumState`.
- `agents/scrum_team/tools/scrum.py` — `REPO_STATE_KEYS`, `save_state_to_repo`.
- `agents/scrum_team/tools/budget.py` — `create_sprint_report()` (transcript-excerpt linking, US-0003).

## 4. Steps / Milestones
1. Add `transcript` field to `ScrumState`.
2. Widen the history callback gate to capture all agent names, tagging each entry with `agent_name`/`role`.
3. Add `transcript` to `REPO_STATE_KEYS` so it's included in persisted state.
4. Implement trimming/summarization logic bounded by a configurable size threshold (US-0004).
5. Link a transcript excerpt from `create_sprint_report()` (US-0003).

## 5. Testing / Verification
- Unit tests per US-0001/US-0002/US-0003/US-0004 acceptance criteria.
- Regression test confirming existing Orchestrator-only capture behavior still works identically before this change is merged.
- Manual verification: run a real sprint via `run.sh cli`, inspect the persisted state file for non-Orchestrator entries.

## 6. Risks & Mitigations
- Risk: full capture inflates token usage on history replay. Mitigation: US-0004 trimming, tested with an oversized synthetic transcript.
- Risk: persisting sensitive tool arguments (e.g. API responses) in plaintext transcript. Mitigation: reuse existing UTF-8/safety handling already present in `agent.py`'s history management; avoid persisting raw secrets by scoping capture to conversational content, not full tool I/O.

## 7. Rollout / Rollback
- Ships as an additive change to `ScrumState` (new optional field) — safe to roll forward without a data migration. Rollback is a straightforward revert since no existing field is renamed or removed.

## 8. References
- EP-0001, US-0001, US-0002, US-0003, US-0004.
