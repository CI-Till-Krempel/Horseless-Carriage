# Requirements Documentation

Use this section to capture product requirements, scope, and acceptance criteria.

Suggested artifacts
- Product Requirements Document (PRD)
- Software Requirements Specification (SRS)
- Non-functional requirements (NFRs)
- Traceability from requirements to stories and tests

How to contribute
1. Start from the provided templates.
2. Keep each requirement atomic and testable; add acceptance criteria.
3. Link related user stories from `../stories` and ADRs from `../architecture`.

Templates
- TEMPLATE-PRD.md — High-level product vision, goals, personas, and success metrics.
- TEMPLATE-SRS.md — Detailed functional/non-functional requirements, constraints, and interfaces.
- TEMPLATE-ISSUE.md — A documented gap (e.g. a mandatory process rule that isn't actually enforced in
  code yet). Filed here via `upsert_issue` (ISSUE-XXXX) and driven through the same 5-stage story
  pipeline (`advance_story_stage`) as a User Story once picked up — see `RELEASE.md` "Story workflow".
