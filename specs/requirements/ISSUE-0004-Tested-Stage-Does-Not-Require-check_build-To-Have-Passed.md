# Issue

- Issue ID: ISSUE-0004
- Title: Tested Stage Does Not Require check_build to Have Passed
- Status: Done
- Priority: Must
- Owner: Scrum Team
- Last Updated: 2026-07-24

## Overview
`QA_PROMPT` (MANDATORY, line 388): "Call `check_build()` for every story before marking it Tested -
it actually attempts [the install/build]." The STORY WORKFLOW table defines TESTED as "`check_build()`
passes and test strategy/coverage is verified," and the SPRINT CLOSE SEQUENCE (step 4) says "QA runs
`check_build()`, then calls `advance_story_stage(..., "Tested")`." `check_build()`
(`agents/scrum_team/tools/quality.py`) is a real mechanical check (it actually runs
`pip install --dry-run`/`npm install --dry-run` against the repo) - but nothing ties its result to
the "Tested" transition. `advance_story_stage` accepts "Tested" purely on ordering + ownership
grounds, whether or not `check_build` was ever called, and whether or not it passed. This is the
exact class of bug `check_build` was built to catch (a broken `requirements.txt`) sailing straight
through to "Tested" if QA simply doesn't bother calling it.

## Acceptance Criteria
- `advance_story_stage(title_or_id, "Tested")` refuses unless the most recent `check_build()` result
  recorded in state has `"passing": True` (or `"passing": None` for stacks it can't check, per
  `check_build`'s own "not checked" fallback - that case should not block).
- Rejection message distinguishes "check_build was never called" from "check_build was called and
  failed," each with a concrete next action.
- A test exists: no `check_build` result recorded -> "Tested" transition errors; a failing result
  recorded -> errors; a passing (or "not checked") result recorded -> succeeds.

## Notes
- Where the gap currently lives: `check_build` in `agents/scrum_team/tools/quality.py` returns its
  result to the caller but (as far as this audit found) doesn't persist it into `tool_context.state`
  anywhere `advance_story_stage` could read it back - that persistence is itself part of the fix.
- Traces back to `agents/scrum_team/prompts.py` line 388 (`QA_PROMPT`), the STORY WORKFLOW table's
  TESTED row, and `ORCHESTRATOR_PROMPT`'s SPRINT CLOSE SEQUENCE step 4.

## Test Approach
- Unit test mocking `check_build`'s underlying `_run` call to simulate pass/fail, then asserting
  `advance_story_stage(..., "Tested")`'s behavior in each case, following the mocking pattern already
  used in `test_quality.py`.

## Resolution
- `check_build()` (`agents/scrum_team/tools/quality.py`) now persists `{"checked", "passing"}` into
  `tool_context.state["last_check_build"]` on every call, including the "no manifest found" case
  (`passing: None`, not blocking).
- `advance_story_stage(..., "Tested")` refuses with a distinct message for "never called" vs. "last
  result failed", and treats `passing: None` (nothing to check) as acceptable.
- Test: `test_quality.py::test_check_build_persists_result_for_the_tested_gate` and
  `test_check_build_persists_not_checked_result`;
  `test_requirements.py::TestAdvanceStoryStageGates::test_tested_requires_qa_review_call_and_passing_build`
  and `test_tested_blocked_when_last_check_build_failed`.
