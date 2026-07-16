# User Story

- Story ID: US-0003
- Title: Expose Transcript in Sprint Report
- Status: Done
- Priority: Should
- Owner: Quality Guardian
- Last Updated: 2026-07-16

## As a Quality Guardian, I want a condensed transcript excerpt linked from the sprint report, so that reviewers can trace which agent made which decision.

## Acceptance Criteria
- Given a persisted transcript (per US-0002), when `create_sprint_report()` runs, then the report includes a link or excerpt pointing to the full transcript location.
- Given a very long transcript, when it's summarized for the report, then key decision points (not just the last N messages) are represented.
- Edge case: no transcript exists yet — report generation still succeeds, noting transcript unavailability rather than failing.

## Notes
- Parent epic: EP-0001.
- Design/technical notes: touch point is `create_sprint_report()` in `agents/scrum_team/tools/budget.py`, which currently builds the report from budget/token usage/retro actions/story estimates only.

## Test Approach
- Unit test asserting the generated report markdown contains a transcript reference when a transcript exists.
- Unit test asserting graceful behavior when transcript state is absent.
