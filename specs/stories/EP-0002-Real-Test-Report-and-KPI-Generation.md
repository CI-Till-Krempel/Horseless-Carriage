# Epic

- Epic ID: EP-0002
- Title: Real Test-Report and KPI Generation
- Status: Draft
- Priority: Must
- Owner: Quality Guardian
- Last Updated: 2026-07-16

## Overview
`agents/scrum_team/tools/quality.py`'s `calculate_kpis()` currently returns a hardcoded dummy dict (e.g. `test_coverage: 0.9`, fixed complexity and vulnerability-scan numbers) with an explicit "dummy data for now" comment. Every sprint report built on top of this is reporting fabricated quality metrics. This epic replaces that with real, execution-derived test/coverage/complexity/security data from the target product repo.

## User Stories / Features
- US-0005 — Execute Test Suite and Collect Coverage
- US-0006 — Compute Real Maintainability Metrics
- US-0007 — Run Security Vulnerability Scan
- US-0008 — Fail Gracefully When Test Tooling Unavailable

## Acceptance Criteria
- `calculate_kpis()` no longer returns any hardcoded/dummy values.
- Test coverage numbers come from actually executing the target repo's test suite with coverage.
- Complexity and vulnerability-scan metrics come from real static analysis/scanning tools, not fixed constants.
- When the target repo lacks the expected test tooling, the KPI result clearly reports a degraded/partial status instead of fabricating numbers.

## Notes
- Primary touch point: `agents/scrum_team/tools/quality.py:calculate_kpis()`.
- Reuse the subprocess-execution pattern (`_run`) already used in `agents/scrum_team/tools/github.py` rather than introducing a second way to shell out.
- Execution target is `_configured_repo_root(tool_context)` (see `agents/scrum_team/tools/base.py`), i.e. the product repo, not this tooling repo.
- This is the "Test Report" artifact identified as entirely missing in the prior sprint-artifact audit.

## Roadmap
- Targeted for v0.1 — Trust & Integrity Fixes (see `specs/ROADMAP.md`).
