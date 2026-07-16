# Implementation Plan

- Plan ID: IP-0002
- Title: Real Test-Report and KPI Generation
- Epic: EP-0002
- Status: Draft
- Owner: Quality Guardian
- Last Updated: 2026-07-16

## 1. Objective
Replace `calculate_kpis()`'s hardcoded dummy data with metrics derived from actually executing tests, static analysis, and security scanning against the target product repo.

## 2. Approach
Reuse the subprocess-execution pattern (`_run`) already established in `agents/scrum_team/tools/github.py` rather than inventing a new one. For each metric (coverage, complexity, vulnerabilities), shell out to the appropriate tool inside `_configured_repo_root(tool_context)`, parse its structured output (JSON/XML where available), and populate the KPI dict from real data. Every metric independently degrades to an explicit "unavailable" flag rather than a fabricated value when its tool isn't present — this is the core fix for the "dummy data for now" comment.

## 3. Affected Components / Files
- `agents/scrum_team/tools/quality.py` — `calculate_kpis()`.
- `agents/scrum_team/tools/base.py` — `_configured_repo_root` (read, not modified).
- `agents/scrum_team/tools/github.py` — `_run` (reused, not modified).

## 4. Steps / Milestones
1. Extract/adapt `_run` into a shared helper usable from `quality.py` (or import it directly) so both modules use one subprocess pattern.
2. Implement coverage collection: execute `pytest --cov` in the target repo, parse coverage percentage and failure counts (US-0005).
3. Implement complexity collection via a static analysis tool, with an explicit "not available" path for unsupported languages (US-0006).
4. Implement vulnerability scanning, with an explicit "not performed" path on scanner absence/failure (US-0007).
5. Add graceful-degradation handling so any subset of the above being unavailable doesn't crash `calculate_kpis()` or downstream `create_sprint_report()` (US-0008).
6. Remove the dummy-data comment and hardcoded values entirely.

## 5. Testing / Verification
- Unit tests per US-0005–US-0008 acceptance criteria, using mocked subprocess calls for deterministic output.
- Test each tool-unavailable path independently and all three simultaneously.
- Manual verification: run KPI calculation against this repo itself (which has real pytest tests) and confirm the coverage number is plausible, not the old fixed `0.9`.

## 6. Risks & Mitigations
- Risk: running real test suites during sprint reporting adds latency/cost. Mitigation: document expected runtime in the tool's docstring; consider a timeout with graceful degradation.
- Risk: target repos vary widely in toolchain (language, test framework). Mitigation: explicit "not available" reporting (US-0008) rather than assuming a single toolchain.

## 7. Rollout / Rollback
- Since `calculate_kpis()` is called from within `create_sprint_report()`, roll out behind a straightforward function replacement — no schema migration needed. Rollback is reverting the function body; downstream consumers only depend on the dict shape, which is preserved.

## 8. References
- EP-0002, US-0005, US-0006, US-0007, US-0008.
