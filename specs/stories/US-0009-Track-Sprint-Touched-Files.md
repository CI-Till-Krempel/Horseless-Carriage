# User Story

- Story ID: US-0009
- Title: Track Sprint-Touched Files
- Status: Draft
- Priority: Must
- Owner: Scrum Master
- Last Updated: 2026-07-16

## As a Scrum Master, I want every file write/commit made during a sprint recorded in state, so we can verify the release PR includes all of them.

## Acceptance Criteria
- Given any write path in `tools/docs.py` (`upsert_prd`, `upsert_srs`, `upsert_adr`, `create_from_template`) or `tools/requirements.py` (`upsert_story`, `upsert_epic`, `update_roadmap`), when it writes a file, then the file's repo-relative path is appended to a `sprint_files_touched` list in `ScrumState`.
- Given a sprint with no writes yet, when state is inspected, then `sprint_files_touched` is an empty list, not missing/undefined.
- Edge case: the same file is written multiple times in one sprint — it appears once in the tracked list, not duplicated per write.

## Notes
- Parent epic: EP-0003.
- Design/technical notes: requires adding `sprint_files_touched: List[str]` to `ScrumState` (`agents/scrum_team/state.py`) and threading a small "record this path" call through each write helper.

## Test Approach
- Unit test per write-path function, asserting the target path lands in `sprint_files_touched` after the call.
- Test de-duplication when the same path is written twice.
