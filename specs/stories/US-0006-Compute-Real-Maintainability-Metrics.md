# User Story

- Story ID: US-0006
- Title: Compute Real Maintainability Metrics
- Status: Draft
- Priority: Should
- Owner: Quality Guardian
- Last Updated: 2026-07-16

## As a Quality Guardian, I want `code_complexity` computed via real static analysis instead of the hardcoded `10`, so that maintainability KPIs are trustworthy.

## Acceptance Criteria
- Given a target repo, when the KPI tool runs, then complexity is computed via an actual static analysis tool appropriate to the repo's language, not a fixed constant.
- Given repos of varying size/complexity, when metrics are computed, then the reported number visibly varies accordingly (i.e., it's not just a different hardcoded constant).
- Edge case: an unsupported language/toolchain reports "not available" rather than a fabricated number.

## Notes
- Parent epic: EP-0002.
- Design/technical notes: touch point is `calculate_kpis()` in `agents/scrum_team/tools/quality.py`, replacing the hardcoded complexity value called out in the "dummy data for now" comment.

## Test Approach
- Unit test with a mocked analysis tool call, asserting the parsed complexity value flows through to the KPI result unchanged.
- Test the "not available" path for an unsupported repo type.
