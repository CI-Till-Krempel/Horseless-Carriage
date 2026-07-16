# User Story

- Story ID: US-0004
- Title: Trim Transcript for Token Budget
- Status: Draft
- Priority: Must
- Owner: ScrumOrchestrator
- Last Updated: 2026-07-16

## As a ScrumOrchestrator, I want long transcripts truncated/summarized before re-injection, so that history replay doesn't blow the sprint token budget.

## Acceptance Criteria
- Given a transcript that has grown beyond a configured size threshold, when it would be re-injected into context, then older entries are truncated or summarized rather than replayed in full.
- Given `SPRINT_TOKEN_BUDGET`/`SPRINT_USD_BUDGET` enforcement (`tools/budget.py`), when transcript capture is enabled, then it does not cause budget checks to fail purely due to transcript-replay overhead.
- Edge case: a transcript exactly at the threshold is handled without an off-by-one truncation error.

## Notes
- Parent epic: EP-0001.
- Design/technical notes: coordinate with the existing budget-callback logic (`check_cost_budget_callback` in `agents/scrum_team/agent.py`, and `agents/scrum_team/tools/budget.py`) so transcript growth is accounted for rather than fought against.
- Risk called out in EP-0001: naive full-transcript capture could itself blow the token/USD budget — this story is the mitigation.

## Test Approach
- Unit test with a synthetic oversized transcript, asserting the trimmed/summarized version stays under the configured threshold.
- Test that recent, high-relevance entries are preserved over older ones when trimming.
