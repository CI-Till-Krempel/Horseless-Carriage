# User Story

- Story ID: US-0013
- Title: Derive Changelog Content from Sprint State
- Status: Draft
- Priority: Should
- Owner: Scrum Master
- Last Updated: 2026-07-16

## As a Scrum Master, I want the generator to pull from `sprint_backlog`/`decision_log` (state), so entries are accurate without manual authoring.

## Acceptance Criteria
- Given completed items in `sprint_backlog`, when the changelog entry is generated, then each completed story/fix is represented as a line item.
- Given entries in `decision_log`, when relevant to user-facing behavior, then they inform the changelog wording (not verbatim decision-log dumps, but grounded in it).
- Edge case: a backlog item marked "Done" but missing a title/description is still represented (falling back to its ID) rather than silently omitted.

## Notes
- Parent epic: EP-0004.
- Design/technical notes: reads `ScrumState.sprint_backlog` and `ScrumState.decision_log` (`agents/scrum_team/state.py`) as the source of truth, feeding US-0012's generation tool — this story is specifically about the extraction/derivation logic, not the file-writing mechanics.

## Test Approach
- Unit test with a fixture `sprint_backlog` containing a mix of Done/In-Progress items, asserting only Done items are surfaced.
- Unit test for the fallback-to-ID behavior on incomplete backlog entries.
