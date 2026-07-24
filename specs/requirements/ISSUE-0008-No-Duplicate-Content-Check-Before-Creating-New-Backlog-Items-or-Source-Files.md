# Issue

- Issue ID: ISSUE-0008
- Title: No Duplicate-Content Check Before Creating New Backlog Items or Source Files
- Status: Done
- Priority: Could
- Owner: Scrum Team
- Last Updated: 2026-07-24

## Overview
`PO_PROMPT` (MANDATORY, line 201): "Before creating new requirements or stories, check the `specs/`
folder in the repository for existing PRDs, ADRs, or User Stories to ensure continuity and avoid
duplication." `DEV_PROMPT` (MANDATORY, line 344): "Before proposing or implementing any work, check
the existing repository content (specs, code, state) to avoid duplicating or overwriting existing
work." `ORCHESTRATOR_PROMPT`'s "EXISTING WORK CHECK" (line 86) says something similar for the
initial setup. None of these have a mechanical backstop - `upsert_story`/`upsert_epic`/`upsert_issue`/
`write_file` all succeed regardless of whether an equivalent title/file already exists, relying
entirely on the model actually running `list_docs`/`read_doc` first and drawing the right conclusion.
This is the lowest-priority gap in this audit (a real duplicate-detection feature is inherently
fuzzy - similar titles worded differently, overlapping-but-not-identical code - so a naive exact-
match check would give false confidence without covering the real failure mode), but it's worth
tracking since duplicate/conflicting story files were an observed problem in earlier eval runs.

## Acceptance Criteria
- At minimum, `upsert_backlog_item` warns (not necessarily blocks, given the false-negative risk of
  a naive check) when a new item's title is a near-exact match (e.g. case-insensitive exact match,
  or matches after stripping punctuation) of an existing `product_backlog` entry's title.
- `write_file` similarly surfaces (in its return value) when it's about to overwrite an existing file
  that has unrelated content, rather than silently clobbering it.
- A test exists for the near-exact-title-match warning path in `upsert_backlog_item`.

## Notes
- Where the gap currently lives: no file implements any duplicate/near-duplicate check today -
  `upsert_backlog_item` (`agents/scrum_team/tools/requirements.py`) and `write_file`
  (`agents/scrum_team/tools/docs.py`) both proceed unconditionally.
- Deliberately scoped down from "detect all duplicates" to "flag exact/near-exact title matches and
  file overwrites" - true semantic dedup is out of scope for a mechanical check and would need a
  different approach (e.g. human review) rather than tooling.
- Traces back to `agents/scrum_team/prompts.py` lines 86, 201, 344.

## Test Approach
- Unit test: `upsert_backlog_item` called twice with the same (or near-identical) title but
  different IDs; assert the second call's response includes a warning field, without necessarily
  rejecting it outright.
