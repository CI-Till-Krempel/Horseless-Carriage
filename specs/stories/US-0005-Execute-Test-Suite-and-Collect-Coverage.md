# User Story

- Story ID: US-0005
- Title: Execute Test Suite and Collect Coverage
- Status: Done
- Priority: Must
- Owner: Quality Guardian
- Last Updated: 2026-07-16

## As a Quality Guardian, I want a tool that runs pytest with coverage against the target repo and parses real results, so that `test_coverage` reflects actual code state.

## Acceptance Criteria
- Given a target repo with a pytest suite, when the new tool runs, then it executes `pytest --cov` inside `_configured_repo_root(tool_context)` and parses the real coverage percentage.
- Given a test run with failures, when results are parsed, then failure counts are surfaced alongside coverage, not swallowed.
- Edge case: a target repo with zero tests reports 0%/no-tests explicitly, rather than a stale or default number.

## Notes
- Parent epic: EP-0002.
- Design/technical notes: reuse the `_run` subprocess-execution pattern from `agents/scrum_team/tools/github.py` instead of introducing a second way to shell out.

## Test Approach
- Unit test with a mocked subprocess call returning sample pytest-cov output, asserting correct parsing.
- Integration-style test against a small fixture repo with a known coverage percentage.
