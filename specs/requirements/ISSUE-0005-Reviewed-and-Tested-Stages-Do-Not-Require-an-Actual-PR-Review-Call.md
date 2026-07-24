# Issue

- Issue ID: ISSUE-0005
- Title: Reviewed and Tested Stages Do Not Require an Actual PR Review Call
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-24

## Overview
Both `QA_PROMPT` (line 402) and `ARCH_PROMPT` (line 435) say (MANDATORY): "Review Pull Requests from
a [quality/architectural] perspective using `gh_pr_review` or `gh_pr_comment`." The STORY WORKFLOW
table's REVIEWED row is owned by Architect and is meant to represent "Architectural/technical review
of the implementation is complete." But `advance_story_stage(title_or_id, "Reviewed")` (and
"Tested") only checks that the caller's `agent_name` matches `STAGE_OWNERS[stage]` - it never checks
that a `gh_pr_review`/`gh_pr_comment` call was actually made for this story's PR. Architect (or QA)
can call `advance_story_stage(..., "Reviewed")` (or "Tested") having never left a single review
comment; the "review" is then nothing but the role's name being the caller, not evidence any review
happened.

## Acceptance Criteria
- `advance_story_stage(title_or_id, "Reviewed")` refuses unless at least one `gh_pr_review`/
  `gh_pr_comment` call attributed to Architect is recorded for this story's PR since it reached
  Implemented.
- Same pattern for `advance_story_stage(title_or_id, "Tested")` and QA.
- A test exists for each stage: no review/comment call recorded -> error; after one is recorded for
  the right story and role -> ok.

## Notes
- Where the gap currently lives: `advance_story_stage` in `agents/scrum_team/tools/requirements.py`
  has no reference to PR review/comment history at all; `gh_pr_review`/`gh_pr_comment`
  (`agents/scrum_team/tools/github.py`) don't record which story they were about.
- Needs a way to associate a review/comment call with a specific story - likely a `title_or_id`
  parameter threaded through `gh_pr_review`/`gh_pr_comment` (or inferred from the currently active
  story in state), recorded similarly to how `_record_touched_file` tracks file writes.
- Traces back to `agents/scrum_team/prompts.py` lines 402 (`QA_PROMPT`) and 435 (`ARCH_PROMPT`), and
  the STORY WORKFLOW table's REVIEWED/TESTED rows.

## Test Approach
- Unit tests per stage, seeding/omitting a recorded review call and asserting the corresponding
  `advance_story_stage` transition's accept/reject behavior.
