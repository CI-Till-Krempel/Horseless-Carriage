# User Story

- Story ID: US-0002
- Title: Persist Full Transcript to State Repo
- Status: Done
- Priority: Must
- Owner: Product Owner
- Last Updated: 2026-07-16

## As a Product Owner, I want the complete transcript persisted to the state repo, so that history survives restarts and is reviewable by stakeholders.

## Acceptance Criteria
- Given a transcript accumulated during a sprint (per US-0001), when the sprint state is saved, then the transcript is written to the state repo via the existing persistence path, not left only in the ADK session's in-memory/sqlite store.
- Given a container restart, when the session resumes, then previously persisted transcript entries are still present.
- Edge case: an empty transcript (no sub-agent turns yet) persists without error.

## Notes
- Parent epic: EP-0001.
- Design/technical notes: reuse `save_state_to_repo` (`agents/scrum_team/tools/scrum.py`) and its `REPO_STATE_KEYS` allowlist — the new transcript field needs to be added to that allowlist to actually be written.

## Test Approach
- Integration test: populate transcript state, call the save path, assert the file exists in the target repo with expected content.
- Test that keys not in `REPO_STATE_KEYS` are correctly excluded, to confirm the new field was added deliberately rather than by an allowlist bypass.
