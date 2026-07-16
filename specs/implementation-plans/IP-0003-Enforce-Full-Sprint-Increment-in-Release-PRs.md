# Implementation Plan

- Plan ID: IP-0003
- Title: Enforce Full Sprint Increment in Release PRs
- Epic: EP-0003
- Status: Draft
- Owner: Product Owner
- Last Updated: 2026-07-16

## 1. Objective
Guarantee that a release PR actually contains every change made during the sprint, closing the gap where `create_release_pr()` currently trusts `git_push(add_all=True)` blindly.

## 2. Approach
Add a `sprint_files_touched` list to `ScrumState`, populated incrementally by every write helper in `tools/docs.py`/`tools/requirements.py`. At release time, `create_release_pr()`/`git_push()` compare `sprint_files_touched` against the real `git status --porcelain`/`git diff --name-only` output before pushing, surfacing a clear warning on any mismatch (tracked-but-not-in-diff, or in-diff-but-untracked) instead of proceeding silently.

## 3. Affected Components / Files
- `agents/scrum_team/state.py` — new `sprint_files_touched: List[str]` field.
- `agents/scrum_team/tools/docs.py` — `upsert_prd`, `upsert_srs`, `upsert_adr`, `create_from_template` (each records its output path).
- `agents/scrum_team/tools/requirements.py` — `upsert_story`, `upsert_epic`, `update_roadmap` (each records its output path).
- `agents/scrum_team/tools/github.py` — `create_release_pr`, `git_push`.

## 4. Steps / Milestones
1. Add `sprint_files_touched` to `ScrumState`.
2. Add a small shared helper (e.g. `_record_touched_file(tool_context, path)`) and call it from every write path listed above (US-0009).
3. In `create_release_pr()`, before calling `git_push`, run `git status --porcelain` and `git diff --name-only` against the target repo and compare to `sprint_files_touched` (US-0010).
4. Surface a clear warning/error on mismatch; require explicit acknowledgment to proceed if mismatches are found.
5. Extend the pre-push check to catch uncommitted/untracked sprint-related files specifically, distinguishing them from unrelated stray local changes (US-0011).

## 5. Testing / Verification
- Unit tests per US-0009/US-0010/US-0011 acceptance criteria.
- Integration test: simulate a sprint writing several files via the docs/requirements tools, then call `create_release_pr` and assert the diff-check passes cleanly.
- Regression test: deliberately omit recording one write, assert the mismatch is caught.

## 6. Risks & Mitigations
- Risk: false positives from build artifacts or unrelated local edits being flagged as mismatches. Mitigation: US-0011 explicitly distinguishes sprint-tracked files from unrelated stray changes rather than treating all working-tree diffs as sprint scope.
- Risk: performance overhead of running additional git commands at release time. Mitigation: these are single, cheap git subprocess calls, negligible relative to the LLM calls already in the release flow.

## 7. Rollout / Rollback
- Additive `ScrumState` field plus new verification logic gated at the point `create_release_pr` already runs — no external interface changes. Rollback is a straightforward revert of the verification step, leaving `git_push(add_all=True)` behavior unchanged.

## 8. References
- EP-0003, US-0009, US-0010, US-0011. Prerequisite for EP-0004 (Changelog Generation).
