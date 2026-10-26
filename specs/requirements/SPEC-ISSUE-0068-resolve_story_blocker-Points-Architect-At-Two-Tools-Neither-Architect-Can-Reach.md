# Issue

- Issue ID: SPEC-ISSUE-0068
- Title: `resolve_story_blocker`'s Own Rejection Message Points Architect At Two Recovery Paths Neither Architect Can Reach, Deadlocking The Blocker For A Full Sprint
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-26

## Overview
Reported (`0.1.0-run71`'s `report.md`): the eval stopped early after only 3 of 5 sprints -
`Stop reason: blocked_unresolved_across_sprint` - because US-0002 stayed BLOCKED across the whole
of Sprint 3, burning that sprint's entire 5,529,051-token budget (111% of its 5,000,000 budget,
per Sprint 3's own `create_sprint_report` call, `transcript.md:10077`) for zero net story progress.
`manifest.json` confirms no state corruption caused this - `stages_completed` for US-0002 is
identical (`['Draft','Ready','Implemented','Reviewed']`) at the end of Sprint 2 and Sprint 3; it
simply never moved past Reviewed because the blocker raised at the top of Sprint 3 was never
cleared.

**Confirmed mechanism - a structural dead end, not a confabulation this time.** SPEC-ISSUE-0060's fix
(now in production, and working exactly as designed here) requires `resolve_story_blocker` to see a
fresh, mechanically-tracked test run since the blocker was raised before it will accept any
resolution text for a `tested_stage_failure` blocker (`agents/scrum_team/tools/requirements.py:
2522-2547`). In this run, Architect is the *only* role with `resolve_story_blocker` in its tool
list (`agents/scrum_team/agent.py:2917-2936`), and every one of its 3 attempts was correctly
refused with the same message (`transcript.md:8790`, `8902`, `8992`):

> `'US-0002' was blocked by a real failing test run - resolve_story_blocker refuses to clear this
> without a fresh test run actually having happened since the blocker was raised. Call
> calculate_kpis (or fix the code and retry advance_story_stage) first - a claim with no new test
> run behind it will not be accepted.`

Both suggested recovery paths are unreachable by the role being told to take them:
- **`calculate_kpis`** is the *only* tool that bumps `test_suite_run_count` outside of
  `advance_story_stage`'s own Tested gate (see SPEC-ISSUE-0060's resolution note: "it's in
  `ALWAYS_PHASE_ALLOWED_TOOLS`, so it's callable even while BLOCKED" - true for the *phase* gate,
  but irrelevant to *role* access). It is in **ScrumMaster's** tool list only
  (`agents/scrum_team/agent.py:2856`) - absent from Architect's (2917-2936), DevTeam's (2863-2895),
  and QA's (2897-2915).
- **"retry advance_story_stage"** is flatly impossible while the story is BLOCKED -
  `raise_story_blocker`'s own guard refuses any `advance_story_stage` call outright with *"'US-0002'
  is already BLOCKED - call resolve_story_blocker first if this has been answered"*
  (`agents/scrum_team/tools/requirements.py:2379`), which is exactly what fired in this run
  (`transcript.md:8756`).

`transcript.md` events 223-290 (lines 8104-9015) show QA, DevTeam, and Architect cycling the loop
textbook-perfectly: QA denies review and transfers to DevTeam; DevTeam edits `app.py`/writes a
commit ("fix: ensure pytest passes cleanly for US-0002", `transcript.md:8511`) and transfers to QA;
QA re-runs `check_build` (`transcript.md:8828-8843` - `"command": "pip install -r
requirements.txt"`, no pytest invocation at all per `check_build`'s own design, see its docstring in
`agents/scrum_team/tools/quality.py:395-421`) and transfers to Architect; Architect calls
`resolve_story_blocker`, gets refused with the message above, tries `transfer_to_agent` to DevTeam
(blocked immediately by the existing transfer-loop breaker, `transcript.md:8803-8807`) or QA, and
the cycle repeats. **ScrumMaster is never once pulled into this loop** across all 18 events of it -
nothing in any role's prompt or in the rejection message itself suggests transferring to ScrumMaster
to get a real `calculate_kpis` run. ScrumMaster only calls `calculate_kpis` once in the whole sprint,
at event 297 (`transcript.md:9093`), as part of its own unrelated end-of-sprint KPI/report routine -
by which point the sprint is already closing. That one real run is also a second piece of evidence
the "fix" never worked: `tests_run: 3, tests_failed: 1` (`transcript.md:9116-9117`) - the identical
1-failing-test count from the original block, confirming DevTeam's commit never actually fixed
`test_add_task` (the global `task_counter`/list `counter` in `app.py`, written at
`transcript.md:7126`, is never reset between tests the way the `lists` dict is in the pytest fixture
at `transcript.md:7165` - a real, separate app-level bug, consistent with `report.md`'s own
"Top Problems" item 3 about test failure loops, but not the subject of this Issue).

This is a different gap from SPEC-ISSUE-0060, not a regression of it: SPEC-ISSUE-0060 closed the
confabulation hole (an unverified claim can no longer slip through - confirmed working as designed
3 times in this very run) but didn't check whether the *recovery path it names in its own rejection
message* is actually reachable by the role it's rejecting. The fix made the gate correctly honest
about requiring real evidence, but gave the blocked role no legitimate way to produce that evidence
without a role it has no reason to invoke.

## Acceptance Criteria
- A role that hits `resolve_story_blocker`'s "no fresh test run" rejection (or the
  "already BLOCKED" rejection from `advance_story_stage`) has at least one tool in its *own* tool
  list, or an explicit, named hand-off instruction in the rejection message itself (e.g. "transfer
  to ScrumMaster and ask it to call calculate_kpis"), that can actually produce the fresh test-run
  evidence the gate requires - callable without first clearing the blocker.
- The rejection message at `agents/scrum_team/tools/requirements.py:2528-2537` no longer tells the
  calling role to do something it structurally cannot do (call a tool absent from its own tool list,
  or retry a call that's unconditionally refused while BLOCKED) - it either names the correct
  hand-off target by role name, or the gap is closed by giving the Architect/DevTeam/QA roles a
  reachable way to trigger `_execute_test_suite_coverage` themselves while a story is BLOCKED.
- A new regression test simulating this exact deadlock shape: a `tested_stage_failure` blocker
  raised while the blocking role (Architect) has no path to `calculate_kpis` and
  `advance_story_stage` is refused outright - asserts that either (a) the rejection message names a
  concretely reachable recovery role/tool, or (b) a reachable tool exists in the blocked role's own
  list that produces the needed fresh-run evidence.
- Does not reopen SPEC-ISSUE-0060's confabulation hole - a resolution claim still must not be accepted
  without a genuinely fresh, passing test run having happened.

## Notes
- Gap lives in the combination of `resolve_story_blocker`'s rejection message
  (`agents/scrum_team/tools/requirements.py:2528-2537`) and the per-role tool lists in
  `agents/scrum_team/agent.py` (ScrumMaster: 2825-2861, DevTeam: 2863-2895, QA: 2897-2915,
  Architect: 2917-2936) - `calculate_kpis` at `agent.py:2856` is ScrumMaster-exclusive, and
  `check_build` (QA-only, `agent.py:2908`) explicitly never runs the test suite - only
  `pip install -r requirements.txt` (`agents/scrum_team/tools/quality.py:395-421`'s own docstring is
  explicit about this: "a later real `pytest` run (see advance_story_stage's Tested
  gate/_execute_test_suite_coverage)" is a separate, later step).
- SPEC-ISSUE-0060's own resolution note anticipated *a* reachability concern - "it's in
  `ALWAYS_PHASE_ALLOWED_TOOLS`, so it's callable even while BLOCKED, unlike `advance_story_stage`
  itself" - but that only addresses the phase gate (`agents/scrum_team/agent.py` phase-transition
  checks), not per-role tool-list membership. Both gates have to pass for a tool call to actually
  succeed; SPEC-ISSUE-0060 verified the first and missed the second.
- This is a confirmed guardrail/message gap, not just "agent behavior under a working guardrail" -
  no amount of better prompting lets Architect call a tool that isn't in its `tools=[...]` list, so
  no retry strategy any model could plausibly generate gets out of this loop on its own. QA did
  attempt the one role-correct move available to it (calling `check_build`, `transcript.md:8828`)
  and even tried calling `resolve_story_blocker` directly (correctly refused as wrong-role,
  `transcript.md:8864`) - the team was not being lazy, it genuinely ran out of reachable tools.
- Distinct from ISSUE-0062 (a permanently-broken `transfer_to_agent` pair burning budget on a
  doomed hand-off) - here the transfers mostly succeed and route sensibly (QA -> DevTeam -> QA ->
  Architect), it's the *destination tool*, not the transfer mechanism, that's unreachable.

## Test Approach
- `agents/scrum_team/tests/test_requirements.py`: construct a story BLOCKED with
  `mechanical_recheck.kind == "tested_stage_failure"` and `test_suite_run_count` not yet advanced,
  then call `resolve_story_blocker` as Architect - assert the returned message either names a
  reachable role/tool, or (once fixed) that an equivalent reachable tool exists for Architect/QA/
  DevTeam to call directly.
- `agents/scrum_team/tests/test_agent.py`: if the fix adds `calculate_kpis` (or an equivalent
  test-run-triggering tool) to Architect's/QA's/DevTeam's tool list, add/extend a role-tool-list
  assertion confirming it's present, alongside confirming ScrumMaster's own list is unaffected by
  the eval-driven scripted team being the primary user of this tool.
- Replay `0.1.0-run71`'s Sprint 3 shape (or an equivalent constructed scenario) against the fixed
  harness and confirm the blocker resolves (or at minimum, the responsible role is told a concrete,
  reachable next step) within the same event budget that previously burned the whole sprint with
  zero progress.

## Resolution
Added `recheck_test_suite` (`agents/scrum_team/tools/quality.py`) - a thin wrapper around the same
`_execute_test_suite_coverage` call `calculate_kpis` already makes (so it persists the same
`test_suite_run_count`/`last_test_suite_result` evidence `resolve_story_blocker` checks), stripped
down to just `status`/`tests_available`/`tests_run`/`tests_failed`/`note` - none of
`calculate_kpis`'s unrelated code-complexity/security-scan/say-do-ratio reporting, which the blocked
role has no reason to trigger just to clear a test-failure blocker. `status` is `"ok"` only when the
suite actually ran AND zero tests failed, `"error"` otherwise (unavailable or still failing) - a
direct yes/no signal, not something the calling role has to derive itself from `tests_failed`.

Added `recheck_test_suite` to Architect's tool list (`agents/scrum_team/agent.py`, next to
`resolve_story_blocker`) and to `ALWAYS_PHASE_ALLOWED_TOOLS` (`agents/scrum_team/helpers.py`) so it's
callable in any sprint phase, including while a story is BLOCKED - the same reachability `calculate_kpis`
already had, now actually usable by the role that needs it.

`resolve_story_blocker`'s rejection message (`agents/scrum_team/tools/requirements.py`) no longer
names `calculate_kpis` or "retry advance_story_stage" - both unreachable for Architect, the only role
that can call `resolve_story_blocker` at all - and instead names `recheck_test_suite`, a tool on
Architect's own list. No cross-role hand-off to ScrumMaster is required to clear this gate anymore.

SPEC-ISSUE-0060's confabulation gate is untouched: `resolve_story_blocker` still checks
`test_suite_run_count`/`last_test_suite_result` against the snapshot taken at block time, regardless
of which tool (`calculate_kpis` or `recheck_test_suite`) produced the fresh run - a claim backed by a
still-failing fresh run is still refused (`test_still_refuses_via_recheck_test_suite_when_the_fresh_run_still_fails`).

Tests added in `agents/scrum_team/tests/test_quality.py`
(`test_recheck_test_suite_reports_pass_fail_counts`,
`test_recheck_test_suite_persists_evidence_for_resolve_story_blocker`) and
`agents/scrum_team/tests/test_requirements.py`
(`test_refusal_names_a_tool_architect_can_actually_call`,
`test_succeeds_once_architect_reaches_a_fresh_passing_run_via_recheck_test_suite`,
`test_still_refuses_via_recheck_test_suite_when_the_fresh_run_still_fails`) - the middle one is the
direct regression test for the 0.1.0-run71 deadlock: Architect alone, with no ScrumMaster
involvement, produces a fresh passing run and `resolve_story_blocker` accepts it. Full suite
(`agents/scrum_team/tests`, via `docker compose run`) passes: 1070 tests, only the one pre-existing
`test_llm_integration.py` network-dependent test fails when run without the `litellm`/`db` services
up, unrelated to this change.
