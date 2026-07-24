# Issue

- Issue ID: ISSUE-0003
- Title: Implemented Stage Does Not Require log_story_tokens
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-24

## Overview
`DEV_PROMPT` (MANDATORY, line 322-324): "Before marking any story Implemented, log how many tokens
it actually took via `log_story_tokens(title_or_id, actual_tokens)`, so the sprint report can show
estimate-vs-actual per story instead of just the estimate guessed at planning time." `log_story_tokens`
(`agents/scrum_team/tools/budget.py`) exists and works, but nothing calls it as a precondition of
`advance_story_stage(title_or_id, "Implemented")` - it's a fully independent tool call the model can
simply skip. The result is exactly what the prompt is trying to prevent: a sprint report with
estimates but no actuals, silently, with no error anywhere in the pipeline.

## Acceptance Criteria
- `advance_story_stage(title_or_id, "Implemented")` refuses unless `story_estimates[title_or_id]`
  (or matching id/title) has an `"actual"` key set - i.e. `log_story_tokens` was called for this
  story first.
- Rejection message names the missing call explicitly (`log_story_tokens(title_or_id, actual_tokens)`)
  so the calling agent has an unambiguous next action, consistent with existing `advance_story_stage`
  rejection messages.
- A test exists: `advance_story_stage(..., "Implemented")` with an estimate but no actual logged ->
  error; after `log_story_tokens` is called for that story -> ok.

## Notes
- Where the gap currently lives: `advance_story_stage` in `agents/scrum_team/tools/requirements.py`
  has no reference to `story_estimates` at all; `log_story_tokens` in
  `agents/scrum_team/tools/budget.py` has no caller-side enforcement either.
- Straightforward compared to ISSUE-0002 (no file-write correlation needed) since
  `story_estimates` is already keyed by `title_or_id` - the check is a simple lookup.
- Traces back to `agents/scrum_team/prompts.py` lines 322-324 (`DEV_PROMPT`, ESTIMATION section).

## Test Approach
- Unit test alongside the existing `advance_story_stage` ordering/ownership tests: seed
  `story_estimates` with only an `"estimate"` key (no `"actual"`), assert the Implemented transition
  errors; add `"actual"` and assert it now succeeds.

## Resolution
- `advance_story_stage(..., "Implemented")` (`agents/scrum_team/tools/requirements.py`) now looks up
  `story_estimates` by story id, title, or the raw `title_or_id` passed in, and refuses unless an
  `"actual"` key is present, naming `log_story_tokens(title_or_id, actual_tokens)` as the fix.
- Test: `test_requirements.py::TestAdvanceStoryStageGates::test_implemented_requires_actual_tokens_logged`
  and `test_implemented_succeeds_once_every_precondition_is_met`.
