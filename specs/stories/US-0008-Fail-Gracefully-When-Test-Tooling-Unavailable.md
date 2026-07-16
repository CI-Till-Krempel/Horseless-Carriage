# User Story

- Story ID: US-0008
- Title: Fail Gracefully When Test Tooling Unavailable
- Status: Draft
- Priority: Must
- Owner: Dev Team
- Last Updated: 2026-07-16

## As a Dev Team member, I want `calculate_kpis` to report a clear degraded/partial status when the target repo lacks test tooling, so the team isn't misled by fabricated numbers.

## Acceptance Criteria
- Given a target repo without pytest, a complexity tool, or a scanner installed/configured, when `calculate_kpis()` runs, then the result explicitly flags which metrics are unavailable rather than substituting a default/dummy value.
- Given a partially-tooled repo (e.g. tests present, no scanner), when KPIs are calculated, then available metrics are reported normally and unavailable ones are flagged independently.
- Edge case: total tooling failure does not crash the sprint report generation — it degrades gracefully.

## Notes
- Parent epic: EP-0002.
- Design/technical notes: this story is the safety net for US-0005/US-0006/US-0007 — it ensures none of those three ever falls back to inventing a number when the real tool isn't available, closing the exact failure mode the original "dummy data for now" comment represented.

## Test Approach
- Unit test simulating each of the three tools being unavailable independently, asserting each surfaces a distinct "unavailable" flag.
- Unit test simulating all three unavailable at once, asserting `create_sprint_report()` still completes successfully.
