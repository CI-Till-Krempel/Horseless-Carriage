# Issue

- Issue ID: SPEC-ISSUE-0069
- Title: Every Test-Running Tool Reports Pass/Fail Counts But Never The Actual Failure Output, Forcing Blind Guessing At A Fix
- Status: Ready
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-26

## Overview
Reported while investigating `0.1.0-run72`: Architect called `recheck_test_suite` (SPEC-ISSUE-0068's
own fix for a BLOCKED-story deadlock) three times in a row while US-0001 was blocked on a
`tested_stage_failure`, and all three returned the identical, uninformative

```json
{"status": "error", "tests_available": true, "tests_run": 6, "tests_failed": 6, "note": null}
```

`note: null` is not a transcript-capture gap - it is exactly what the tool returns. Between each
call, DevTeam rewrote `test_app.py`'s fixtures three separate times (switching between pytest
fixtures, `unittest.TestCase.setUp`, and manual per-test `test_client()` instantiation -
`transcript.md:3843`, `4023`), guessing at "tests interfering with each other" - plausible, but
wrong, and un-falsifiable from the information the tool gave back.

**Confirmed root cause.** I cloned the actual code from the eval repo's PR (#601, branch
`eval-0.1.0-run72/feature/US-0001-create-to-do-list`) and ran the exact test suite DevTeam wrote,
reproducing the identical `6 failed, 6 total`:

```
jinja2.exceptions.TemplateSyntaxError: unexpected '}'
templates/index.html:34
```

The real bug is a typo in `templates/index.html:34`:
`<form action="/lists/{{ list_id }/delete" method="POST" class="d-inline">` - a missing closing
`}}`. Every one of the 6 tests renders `index.html` directly or via a POST-redirect, so all 6 fail
identically on the same template-compile error. **This part is a genuine DevTeam implementation
bug, not a tooling bug** - confirmed by direct reproduction, and unrelated to SPEC-ISSUE-0064's
Gradle/Maven adapter work (the pytest code path is byte-for-byte unchanged by that refactor).

**The tooling gap is real and separate: nothing in the chain ever surfaces *why* tests failed.**
`_PytestRunner.run` (`agents/scrum_team/tools/test_runners.py:266-272`) only populates `note` when
the `--cov` summary regex fails to match pytest's stdout - never when `tests_failed > 0` with a
real pytest run and a real traceback sitting right there in `stdout`. The exact same shape exists in
`_jvm_result` (`test_runners.py:145-172`, shared by Gradle/Maven): `note` is populated only for "no
JUnit XML found" or "no JaCoCo report" - never for "the JUnit XML says N tests failed," even though
Gradle/Maven's own console output (`run_result["stdout"]`) contains the real failure detail.

This blind spot is **pre-existing, not introduced by SPEC-ISSUE-0064** - the pytest logic is
explicitly documented as "today's exact pre-SPEC-ISSUE-0064 logic, unchanged"
(`test_runners.py:217-224`), carried over bug-for-bug during that refactor. It predates even that:
`git show b54c9d3^:agents/scrum_team/tools/quality.py` shows the identical `note is None unless
coverage regex fails` logic before SPEC-ISSUE-0064 existed at all. What makes it newly consequential
is SPEC-ISSUE-0068: `recheck_test_suite` was built specifically to give a BLOCKED role a
self-service way to produce real diagnostic evidence, and it inherited this blind spot, so the tool
built to break exactly this kind of stuck loop returns nothing a model can act on.

The gap then threads through every consumer of this result, none of which has ever had real failure
detail to pass along even if it wanted to:
- `_execute_test_suite_coverage` (`quality.py:171-180`) persists `last_test_suite_result` to state
  with only `available`/`tests_run`/`tests_failed` - no `note` field at all, so even if the adapter
  layer is fixed, `resolve_story_blocker`'s rejection (next point) still can't see it.
- `advance_story_stage`'s Tested-gate rejection for `tests_failed > 0`
  (`agents/scrum_team/tools/requirements.py:1924-1932`) builds its message from only
  `tests_failed`/`tests_run` - contrast with the sibling "no tests ran" branch three lines above
  (`requirements.py:1907-1915`), which *does* interpolate `coverage_result.get('note')`. The
  "tests failed" branch is the one that fires on every real bug, and it's the one missing the detail.
- `resolve_story_blocker`'s "still shows N failing" rejection (`requirements.py:2555-2564`) reads
  `last_result` from state, which (per the point above) never had a `note` field to read in the
  first place.
- `recheck_test_suite` (`quality.py:472-504`) forwards whatever `note` it's given - correctly, but
  there's nothing to forward.

Net effect: across this run's 3 QA `advance_story_stage` rejections and 3 Architect
`recheck_test_suite` calls - 6 total real pytest runs - the actual Jinja2 traceback that would have
pointed straight at `templates/index.html:34` was computed by pytest every single time and
discarded every single time.

## Acceptance Criteria
- When a supported test runner (pytest, Gradle, Maven) executes and `tests_failed > 0`, its result's
  `note` field contains a bounded tail of the real tool output (the part containing the failing
  test name(s) and traceback/exception, not just the summary line) - not `null`.
- `_execute_test_suite_coverage`'s persisted `state["last_test_suite_result"]` includes this `note`,
  so a later `resolve_story_blocker` call (in a different turn/session) can still see it, not just
  the immediate caller of `_execute_test_suite_coverage`.
- `advance_story_stage`'s "N of M tests failed" rejection message includes this diagnostic detail,
  the same way its "no tests ran" sibling branch already includes `coverage_result.get('note')`.
- `resolve_story_blocker`'s "still shows N failing" rejection message includes the diagnostic detail
  from `last_test_suite_result`, when present.
- `calculate_kpis`'s `test_coverage_note` (already wired to forward `coverage_result["note"]`,
  `quality.py:578-579`) picks up the new diagnostic content with no further code change - verify
  this with a test rather than assuming it.
- Does not regress the existing "coverage summary not found" / "no JaCoCo report" notes for the
  passing-but-no-coverage case - those remain when `tests_failed == 0`.
- A regression test replaying this run's shape (pytest output containing a `TemplateSyntaxError`
  traceback, `tests_failed: 6`) asserts the traceback text survives into `recheck_test_suite`'s
  returned `note`, `advance_story_stage`'s rejection message, and `resolve_story_blocker`'s
  rejection message.

## Notes
- Output must stay bounded (a few KB, matching the existing `[-800:]`/`[-4000:]` tail-truncation
  conventions already used elsewhere in `quality.py`/`test_runners.py` for the same reason: don't
  let one verbose tool dump blow up a sprint's token budget) - truncate to a tail, don't paste the
  full raw log.
- Gradle/Maven's own console output (not just the JUnit XML summary) already contains real failure
  detail by default (`gradle test` prints a failure summary with exception type/message per failed
  test; Maven Surefire does the same) - no new flag/plugin is required to get it, unlike JaCoCo for
  coverage.
- This is scoped to surfacing *existing* output the tools already produce, not adding new
  instrumentation, verbosity flags, or a different test invocation - `_PytestRunner`'s existing
  `pytest --cov --cov-report=term -q --no-header` command already prints tracebacks by default (no
  `--tb=no` is set); the fix is capturing what's already there, not asking the tool to produce more.

## Test Approach
- `agents/scrum_team/tests/test_test_runners.py`: a `_PytestRunner` test (no dedicated class exists
  yet for it - add one) with mocked `_run` output containing a real-shaped pytest failure (a
  `FAILURES` section + traceback), asserting `tests_failed > 0` produces a non-null `note` that
  contains the traceback text. Extend `TestGradleRunner`/`TestMavenRunner` with an equivalent case:
  JUnit XML reporting `failures > 0` alongside mocked console stdout containing a failure message,
  asserting `note` surfaces it.
- `agents/scrum_team/tests/test_quality.py`: extend `test_execute_test_suite_coverage_parses_real_output`
  (or add a sibling) to assert `result["note"]` contains the failure detail when `tests_failed > 0`;
  add a test asserting `tool_context.state["last_test_suite_result"]["note"]` is persisted.
- `agents/scrum_team/tests/test_requirements.py`: extend `test_tested_blocked_when_tests_failed` to
  mock a `note`-bearing coverage result and assert the note text appears in `result["message"]`;
  extend `TestResolveStoryBlockerMechanicalRecheck`'s `_FAILING_RUN` fixture with a realistic `note`
  and assert `test_still_refuses_via_recheck_test_suite_when_the_fresh_run_still_fails`'s rejection
  message includes it.
