# User Story

- Story ID: US-0012
- Title: Generate Changelog Entry per Release
- Status: Draft
- Priority: Should
- Owner: Product Owner
- Last Updated: 2026-07-16

## As a Product Owner, I want a tool that generates a `CHANGELOG.md` entry summarizing the sprint's completed stories/fixes, so consumers of the product repo can see what changed per release.

## Acceptance Criteria
- Given a release is being cut, when the new changelog tool runs, then a dated, versioned entry is prepended to `CHANGELOG.md` in the target repo (creating the file if it doesn't exist yet).
- Given multiple releases over time, when entries accumulate, then each is clearly delimited by version and date, in reverse-chronological order.
- Edge case: a sprint with no completed stories still produces a minimal, honest entry (e.g. "no user-facing changes") rather than an empty or fabricated one.

## Notes
- Parent epic: EP-0004.
- Design/technical notes: likely a new function in `agents/scrum_team/tools/github.py` or a new `agents/scrum_team/tools/changelog.py`, using a Keep-a-Changelog-style format.

## Test Approach
- Unit test asserting a new entry is correctly prepended to an existing `CHANGELOG.md`.
- Unit test for first-run behavior when `CHANGELOG.md` doesn't exist yet.
