# Issue

- Issue ID: ISSUE-0002
- Title: Implemented Stage Does Not Verify Real Source Files Were Written
- Status: Done
- Priority: Must
- Owner: Scrum Team
- Last Updated: 2026-07-24

## Overview
`DEV_PROMPT` says (MANDATORY, line 340): "Write the actual source files for each implementation
story via `write_file`... not just a description of what you would write," and the STORY WORKFLOW
table defines IMPLEMENTED as "Real, working code committed and pushed." But `advance_story_stage`
(`agents/scrum_team/tools/requirements.py`) only checks stage ordering and stage ownership before
accepting a transition to "Implemented" - it never checks whether any file was actually written for
that story. `_story_readiness_issues`, the one content-completeness backstop that does exist, only
validates the story's own title/user_story/acceptance_criteria text, not whether corresponding code
exists. A story can go straight from Ready to "Implemented" with nothing but a plan in the chat
transcript, exactly the failure mode the prompt explicitly calls out and tries to talk the model out
of.

## Acceptance Criteria
- `advance_story_stage(title_or_id, "Implemented")` refuses (mirrors existing rejection shape:
  `{"status": "error", "message": ...}`) unless at least one file touch is recorded for that story
  since it reached Ready - `_record_touched_file` (`agents/scrum_team/tools/base.py`) already tracks
  every file `write_file` touches; a mapping is needed from "touched files" to "which story."
- The rejection message points at the concrete missing action (call `write_file` for this story
  before marking Implemented), the same style as `_story_readiness_issues`'s messages.
- Edge case: a legitimate pure-planning/spike story (`DEV_PROMPT`: "Only pure planning/spike stories
  should ever produce a plan with no code") is not blocked - needs an explicit opt-out field on the
  story, not a blanket requirement.
- A test exists: `advance_story_stage(..., "Implemented")` with zero touched files for the story ->
  error; after a `write_file` call attributed to that story -> ok.

## Notes
- Where the gap currently lives: `advance_story_stage` in `agents/scrum_team/tools/requirements.py`
  (around the ordering/ownership checks) has no reference to touched files at all.
- `_record_touched_file` already exists in `agents/scrum_team/tools/base.py` and is called from
  `write_file` and `_update_story_markdown` - the missing piece is associating a touched file with a
  specific story ID/title so this check has something concrete to test against, plus a `spike: true`
  (or similar) opt-out field on the backlog item.
- Traces back to `agents/scrum_team/prompts.py` line 340 (`DEV_PROMPT`) and the STORY WORKFLOW table
  (`ORCHESTRATOR_PROMPT`, IMPLEMENTED row).

## Test Approach
- Unit test in the style of `test_budget.py`'s retro-gate tests: seed `tool_context.state` with a
  story at "Ready" and no touched files, assert `advance_story_stage(..., "Implemented")` errors;
  record a touched file for it and assert the same call now succeeds.

## Resolution
- Added `is_source_file(rel_path)` (`agents/scrum_team/helpers.py`) to tell real code apart from
  `specs/`/`spec-templates/`/`.hc/` documents.
- `advance_story_stage(..., "Implemented")` now counts `sprint_files_touched` entries that pass
  `is_source_file` and refuses unless that count is above `dev_touch_baseline` (a new `ScrumState`
  field, bumped only on success - same "must be NEW" pattern as `retro_baseline`).
- Added a `spike: true` opt-out on the backlog item for genuine planning/spike stories, per
  `DEV_PROMPT`'s own carve-out.
- Test: `test_requirements.py::TestAdvanceStoryStageGates::test_implemented_requires_real_source_file_written`
  and `test_implemented_spike_story_bypasses_file_write_gate`.
