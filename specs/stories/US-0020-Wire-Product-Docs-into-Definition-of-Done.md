# User Story

- Story ID: US-0020
- Title: Wire Product Docs into Definition of Done
- Status: Draft
- Priority: Should
- Owner: Scrum Master
- Last Updated: 2026-07-16

## As a Scrum Master, I want the DoD and `DEV_PROMPT` to require updating end-user docs when user-facing behavior changes, so product documentation doesn't silently go stale.

## Acceptance Criteria
- Given a story that changes user-facing behavior, when the Dev Team completes it, then `DEV_PROMPT` requires calling `upsert_user_doc` (US-0019) as part of finishing that story, not as an optional afterthought.
- Given the Definition of Done text tracked in `ScrumState.definition_of_done`, when this change ships, then it explicitly lists "end-user docs updated (if user-facing behavior changed)" as a criterion.
- Edge case: a purely internal change (no user-facing behavior change) — the DoD criterion is explicitly satisfied by "not applicable," not silently ignored.

## Notes
- Parent epic: EP-0006. Depends on US-0019 existing first.
- Design/technical notes: touch points are `DEV_PROMPT` in `agents/scrum_team/prompts.py` and the default `definition_of_done` content in `agents/scrum_team/state.py`.

## Test Approach
- Review-based verification of prompt/DoD text changes (not independently unit-testable), plus an integration test asserting a simulated user-facing story completion invokes `upsert_user_doc`.
