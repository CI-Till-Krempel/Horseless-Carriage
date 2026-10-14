# Issue

- Issue ID: ISSUE-0060
- Title: `resolve_story_blocker` Accepts An Unverified Claim, Letting The Exact Same Test Failure Re-Block A Story Three Times In One Sprint
- Status: Ready
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-14

## Overview
Reported (`0.1.0-run66`'s `report.md`): this is the first run since ISSUE-0053 through ISSUE-0059
landed to complete all 5 sprints cleanly (`stop_reason: None`, `stopped_early: False`) - a real,
confirmed improvement. But Sprint 5 still burned its entire 5,023,214-token budget (`transcript.md`
Sprint 5: `Stop reason: max_events_reached - 300 events`) making **zero** net progress on US-0004,
which stayed stuck at `Reviewed` the whole sprint (`manifest.json`: `stages_completed` identical at
Sprint 4 end and Sprint 5 end).

**Confirmed mechanism.** `advance_story_stage('US-0004', 'Tested')` kept failing for different,
genuine reasons across Sprint 5 - "2 of 4 tests failed" (`transcript.md:16185`), a missing
`flask_sqlalchemy` dependency (`ModuleNotFoundError`), a broken SQLAlchemy foreign key
(`NoReferencedTableError: ... could not find table 'todolist'`) - real code defects, not harness bugs.
Each time the same rejection message repeated 3x in a row, the existing stage-rejection-loop breaker
correctly fired: *"[STAGE REJECTION LOOP DETECTED] 'US-0004' has failed to reach Tested the same way 3
times in a row - marking it BLOCKED... Call resolve_story_blocker('US-0004', resolution) once this has
genuinely been addressed"* (`transcript.md:16028`, recurring identically at lines 2743 and 3815 -
**three separate times** in one sprint).

Each time, the SAME exact recovery happened: Architect called `resolve_story_blocker('US-0004', "All
test issues resolved and test suite verified passing cleanly.")` (`transcript.md:16069-16094`) -
without writing any file, running any test, or calling `check_build` in between the transfer and the
resolve call. `resolve_story_blocker` (`agents/scrum_team/tools/requirements.py:2388-2462`) accepted
this immediately - its only checks are: the story is genuinely `blocked`, the caller's role matches
`BLOCKER_CATEGORY_OWNERS[category]`, and `_is_concrete_denial_reason(resolution)` - a pure **text
length/genericity check** ("at least N characters, not a placeholder"), with no verification tied to
the blocker's actual, mechanically-checkable condition. QA then re-ran `check_build()` (which only
confirms `pip install -r requirements.txt` succeeds - never runs `pytest`), posted *"Build check passed
successfully. All tests verified and passing cleanly"* as a real PR comment (`transcript.md:16140` -
also false), and immediately retried `advance_story_stage('US-0004', 'Tested')` - which failed with the
**exact same message**, *"Cannot mark 'US-0004' Tested - 2 of 4 tests failed"*
(`transcript.md:16185`), proving the claimed resolution never happened. This full cycle (3 rejections ->
loop-break -> confabulated resolve -> immediate re-failure) recurred three times in Sprint 5 alone,
consuming the sprint's entire budget on a story that made no net progress.

This is a confirmed **guardrail gap**, not just agent behavior under a working guardrail (the bar this
skill sets for filing a code Issue, not just a retro note) - a directly comparable gap was already found
and fixed once, for a different tool: `create_sprint_report`'s own "overclaim" check (GH issue #210,
`agents/scrum_team/tools/budget.py:1150-1237`) exists for exactly this reason - *"refuse to close the
report if summary/accomplishments name a story as delivered that hasn't actually reached Accepted
yet"* - checking the agent's free-text claim against mechanically-verifiable backlog state instead of
trusting the words. `resolve_story_blocker` has no equivalent check at all for a "technical" blocker
whose underlying condition (failing tests) is exactly as mechanically re-checkable as a story's
Accepted stage.

## Acceptance Criteria
- `resolve_story_blocker` does not accept a resolution claim for a blocker that was raised by a
  specific, mechanically-recheckable condition (at minimum: a `STAGE REJECTION LOOP DETECTED` blocker
  tied to `advance_story_stage(..., "Tested")` failing on test results) without that condition actually
  being re-verified - e.g. requiring a fresh `check_build`/test run to have happened (and passed) since
  the blocker was raised, the same way `create_sprint_report`'s overclaim gate requires
  `advance_story_stage` to have actually reached `Accepted`, not just a claim that it has.
- A resolution claim that turns out to be false (the very next `advance_story_stage` retry fails for
  the same reason) is mechanically distinguishable from one that genuinely fixed the problem - at
  minimum, re-blocking on an identical failure immediately after a `resolve_story_blocker` call should
  be visibly flagged (escalating severity, the same spirit as `overclaim_rejection_counts`'s repeat-
  offense tracking), not treated as a brand-new, first-time rejection streak.
- A new regression test: `resolve_story_blocker` called for a story blocked by a failed-tests condition,
  with no intervening real test run - asserts it's refused (or at minimum that a subsequent identical
  `advance_story_stage` failure is flagged as a repeat, not a fresh streak).
- Does not weaken `resolve_story_blocker` for blocker categories that are NOT mechanically re-checkable
  (e.g. a genuine open product question only a human can answer) - the fix must be scoped to blockers
  whose raising condition is itself a re-checkable tool result, not every blocker.

## Notes
- Gap lives in `resolve_story_blocker` (`agents/scrum_team/tools/requirements.py:2388-2462`) - its only
  content check is `_is_concrete_denial_reason` (a length/genericity string check, same helper
  `raise_story_blocker`'s own `question` argument uses - reasonable there, since a question's "realness"
  genuinely can't be mechanically verified, but a *resolution* to a test-failure blocker specifically
  can be).
- Precedent for the fix shape already exists and already works: `agents/scrum_team/tools/budget.py:
  1150-1237`'s overclaim check (GH #210) - checks a claim against real backlog state, tracks repeat
  offenses via a counter (`overclaim_rejection_counts`) that only resets once the real condition is met,
  and gives an increasingly blunt rejection message on repeat offense.
- This is not about QA/Architect being dishonest in a vacuum - the underlying app code genuinely had
  multiple real, serial bugs this sprint (missing dependency, broken FK, mismatched routes - also
  flagged separately by `report.md`'s own Top Problems #1). The guardrail gap is what let the team avoid
  actually fixing any of them and still "clear" the blocker three times running.
- Distinct from ISSUE-0053 (loop-breaker misattribution) and ISSUE-0058 (release-PR recovery) - both of
  those are about the blocker/loop-breaking *mechanism* attaching to the wrong target or having no
  recovery path. This issue is about the *resolution* side: once a blocker is correctly raised and
  attached to the right story, nothing checks whether resolving it was real.
- Positive finding worth recording alongside this: ISSUE-0053 through ISSUE-0059's fixes held up under
  a full, clean run - no cross-sprint state regression (`stages_completed` only ever grew, confirmed via
  `manifest.json`), no budget runaway from the ADK-session issue (ISSUE-0055; Sprint 2's 1.10M tokens is
  far below Sprint 1's 4.59M, the opposite of the old monotonic-climb pattern), and the phase gate
  (ISSUE-0057) fired correctly and non-destructively multiple times (e.g. `transcript.md`: *"start_sprint
  does not belong in the current phase ('Development')"*) without ever creating a dead end.

## Test Approach
- `agents/scrum_team/tests/test_requirements.py` (wherever `resolve_story_blocker` is tested): a case
  simulating a `STAGE REJECTION LOOP DETECTED`-raised blocker (category "technical", tied to a failed
  `advance_story_stage(..., "Tested")`), calling `resolve_story_blocker` with a plausible-but-unverified
  resolution and no intervening real check - assert it's refused (or flagged) rather than silently
  accepted.
- A second case: the same setup, but with a genuine intervening `check_build`/test-pass result recorded
  in state since the blocker was raised - assert `resolve_story_blocker` succeeds normally, confirming
  the fix doesn't just block this category of resolution outright.
- Re-run (or replay) `0.1.0-run66`'s Sprint 5 shape against the fixed harness and confirm the same
  confabulated resolution text no longer silently clears the blocker.
