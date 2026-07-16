# Implementation Plan

- Plan ID: IP-0004
- Title: Changelog Generation
- Epic: EP-0004
- Status: Draft
- Owner: Product Owner
- Last Updated: 2026-07-16

## 1. Objective
Introduce automated `CHANGELOG.md` generation for the product repo, derived from real sprint state, shipped atomically with each release.

## 2. Approach
Add a changelog-generation function that reads `ScrumState.sprint_backlog` (for completed items) and `ScrumState.decision_log` (for context), formats a Keep-a-Changelog-style dated/versioned entry, and prepends it to `CHANGELOG.md` in the target repo (creating the file on first use). Wire this call into `create_release_pr()` before `git_push`, and record the changelog file path via IP-0003's `sprint_files_touched` tracking so it's covered by that epic's diff verification rather than being treated as an unexpected change.

## 3. Affected Components / Files
- New: `agents/scrum_team/tools/changelog.py` (or a new function in `agents/scrum_team/tools/github.py`).
- `agents/scrum_team/tools/github.py` — `create_release_pr` (calls the new changelog step).
- `agents/scrum_team/state.py` — reads `sprint_backlog`, `decision_log` (no new fields needed).

## 4. Steps / Milestones
1. Implement the core generator: derive changelog line items from `sprint_backlog`, falling back to item ID when title/description is missing (US-0012, US-0013).
2. Implement file I/O: prepend the new entry to `CHANGELOG.md`, creating it with a standard header if absent (US-0012).
3. Handle the empty-sprint case with an honest "no user-facing changes" entry rather than a fabricated one.
4. Wire the generator into `create_release_pr()`, before the push step, and record the changelog path via `_record_touched_file` from IP-0003 (US-0014).
5. Add a failure-isolation path: if changelog generation errors, the release PR still proceeds with a surfaced warning rather than blocking entirely (US-0014 edge case).

## 5. Testing / Verification
- Unit tests per US-0012/US-0013/US-0014 acceptance criteria.
- Integration test: run a simulated release with a fixture `sprint_backlog`, assert `CHANGELOG.md` is updated and included in the same PR diff as other changes.

## 6. Risks & Mitigations
- Risk: changelog wording quality depends on backlog item titles being meaningful. Mitigation: ID-fallback (US-0013) plus this is reviewable in the PR before merge, not auto-merged.
- Risk: coupling changelog generation to release flow could block releases if it errors. Mitigation: explicit failure-isolation step (US-0014) so changelog failure degrades gracefully.

## 7. Rollout / Rollback
- New, additive tool call in the existing release flow. Rollback is removing the call site in `create_release_pr()`; no data migration involved.

## 8. References
- EP-0004, US-0012, US-0013, US-0014. Depends on EP-0003. Feeds EP-0005.
