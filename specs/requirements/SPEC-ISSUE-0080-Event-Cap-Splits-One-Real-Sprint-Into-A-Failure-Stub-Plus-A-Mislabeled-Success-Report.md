# Issue

- Issue ID: SPEC-ISSUE-0080
- Title: A Sprint That Exhausts Its Event Cap Right After Finishing All Work Gets A "Ended Without A Report" Stub, While The Real Success Narrative Is Mislabeled Onto The Next Sprint Number
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-30

## Overview
Investigated `0.1.0-run78` (`gh run view 41022229501`, commit `99db4532225c7881d1481ae31bf4d2316434c91c` -
already includes SPEC-ISSUE-0079's fix). `report.md`'s per-sprint table shows an alternating pattern across
all 5 sprints that looks like the team did real work, then nothing, then real work, then nothing again:

| Sprint | Tokens Used | Stories Planned | Stories Implemented |
|---|---|---|---|
| 1 | 4,888,656 | 1 | 3 |
| 2 | 5,046,845 | 0 | 0 |
| 3 | 5,166,226 | 3 | 1 |
| 4 | 5,357,127 | 0 | 0 |
| 5 | 1,682,077 | 1 | 0 |

Sprints 2 and 4 - "0 stories planned, 0 implemented" - are not empty sprints at all. They are the
harness's own mechanism for letting a sprint finish writing its close-out report one loop iteration late,
but the resulting manifest entries end up **exactly backwards**: the sprint that did all the real work gets
a generic failure-shaped stub, and the sprint that did nothing gets the real, glowing success report.

**Confirmed mechanism.** `_run_one_sprint`'s inner turn loop (`agents/scrum_team/scripts/run_eval.py:781-783`)
breaks on `max_events_reached` with no reserved budget for the close-out sequence
(`calculate_kpis` -> `update_sprint_report` -> `create_release_pr`). `transcript.md` shows Sprint 1's very
last event (event 300 of 300, transcript.md:4650-4757) is `ProductOwner` calling
`advance_story_stage(stage="Accepted", title_or_id="US-0003")` - all three of Sprint 1's planned stories
(US-0001/0002/0003) are fully `Accepted` at that exact moment, but zero events remain for ScrumMaster to
call `create_sprint_report`. The broadened SPEC-ISSUE-0077 backstop
(`_ensure_sprint_report_on_final_halt`, invoked at `run_eval.py:802-834`) then fires because
`sprint_report` is empty, producing `manifest.json`'s Sprint 1 entry:

```
## Mechanically-Rendered Report (Sprint Ended Without A Report)
Scrum Master's own `create_sprint_report` call never ran this sprint, for a reason this harness
didn't specifically distinguish.
```

- a report that reads as a dead end, even though Sprint 1 actually delivered 100% of its planned work.

The outer sprint loop (`for sprint_number in range(1, args.sprints + 1)`, `run_eval.py:989`) then
unconditionally advances to "sprint 2" and sends the scripted `_sprint_message(2, 5)`
(`run_eval.py:996`), regardless of whether Sprint 1's close-out ever actually finished.
`_start_fresh_sprint_session` (`run_eval.py:892-926`) starts this "sprint 2" on a brand-new ADK session
but carries the full prior `session.state` forward verbatim (`sprint_backlog`, `token_usage`, everything) -
so the team is still, mechanically, finishing Sprint 1's own close-out. Confirmed directly: the "Sprint 2"
manifest entry's own report says, verbatim,

```
## Summary
Sprint 2 was successfully executed with excellent team effectiveness, high test coverage, and smooth
completion of all planned items.
...
## Conversation Transcript
Most recent contribution per agent:
- **ProductOwner**: advance_story_stage(stage="Accepted", title_or_id="US-0003")
```

- the exact same `US-0003` Accept call from Sprint 1's event 300 is what "Sprint 2" cites as its own
"most recent contribution." Its per-agent token table is also the smoking gun for the carried-over state:
`DevTeam: 1,180,782`, `Architect: 894,047`, `QA: 755,270` are **identical** to Sprint 1's own final
per-agent totals (`manifest.json` sprint 1 vs sprint 2 `token_usage.agents`) - those three agents did
nothing in "sprint 2"'s 18 events (`event_count: 18`, `stop_reason: sprint_report_produced`); only
ScrumMaster/ScrumOrchestrator/ProductOwner's totals ticked up slightly while they wrote the report. This is
expected per SPEC-ISSUE-0056's resolution - `start_sprint` (the only reset path) was never called in this
iteration (confirmed: no `start_sprint` call anywhere in transcript.md's sprint-2 section,
transcript.md:4761-5191), because it genuinely isn't a new sprint.

**The exact same pattern repeats for Sprints 3/4.** Sprint 3 hits `max_events_reached` at event 300
(transcript.md:5192-10777) with all three of its planned items (US-0007, SPEC-ISSUE-0001, SPEC-ISSUE-0002)
already `Accepted` per `manifest.json`'s `stages_completed`, and gets the same generic
"Sprint Ended Without A Report" stub. "Sprint 4" (23 events, no `start_sprint` call, `DevTeam`/`Architect`/
`QA` token totals identical to Sprint 3's) then writes: `"Sprint 4 concluded successfully with all planned
user stories fully implemented, tested, and accepted... Stories: 3/3 completed this sprint"` - again
describing Sprint 3's actual delivery, labeled as Sprint 4's.

**Why this matters beyond cosmetics.** This eval harness exists specifically so the team's *own*
behavior can be judged release over release (see `RELEASE.md`'s "Team performance evaluation" section).
This run's judge-LLM scored **Team Efficiency: 2/5** and named "token budget exhaustion across multiple
consecutive sprints" its #1 Top Problem, evidenced by "Sprint 3 consumed 5,166,226 tokens and Sprint 4
consumed 5,357,345 tokens" - both of those are the *same real sprint's* carried-over cumulative total,
counted once under each of two manifest entries, one of which (the real "Sprint 4") did zero new product
work and only spent ~191K tokens of its own finishing paperwork. The measurement this harness is built to
produce - genuine per-sprint delivery and efficiency - is corrupted by its own reporting/attribution
layer, independent of how well or badly the team actually performed.

## Acceptance Criteria
- A sprint whose own planned backlog items are already fully `Accepted` by the time its event/duration cap
  is hit (real delivery complete, only the close-out narrative missing) must not be rendered with the
  "Sprint Ended Without A Report" / dead-end-shaped stub - the mechanical fallback needs to distinguish
  "ran out of host-side turns after finishing the work" from "actually stalled."
- A report that a later loop iteration writes while genuinely still closing out a *previous* sprint
  (i.e. no `start_sprint` call happened since the last budget reset) must not be attributed to a new,
  distinct "sprint N+1" in the manifest/report.md as if it were that sprint's own narrative and KPIs -
  either fold it back into the sprint it actually describes, or have `manifest["sprints"]` key sprint
  identity off the team's own `start_sprint`-driven sprint boundary instead of the raw per-loop-iteration
  counter.
- `report.md`'s per-sprint table and "Sprints requested/completed" framing must not imply N independent
  planning cycles happened when some of those N manifest entries are purely close-out continuations of
  the previous entry - this should be visible to a maintainer or judge-LLM reading the report without
  having to cross-reference `transcript.md`.
- New regression test: a scripted scenario where a sprint's event cap lands exactly on the final
  `advance_story_stage(..., "Accepted")` call (no events left for `create_sprint_report`) produces a
  manifest where the real sprint's delivery and its close-out narrative are both attributed to the same
  sprint - not a "0 stories" sprint followed by a mislabeled success report.

## Notes
- Distinct from SPEC-ISSUE-0077 (ensures *some* report exists for any dead end - working as designed here,
  just not distinguishing "finished the work" dead ends from real stalls) and SPEC-ISSUE-0056 (budget reset
  gated on `start_sprint` - also working as designed; the non-reset between sprint 1/2 and 3/4 is *correct*
  given these are the same real sprint, which is exactly why the two manifest entries shouldn't be
  presented as independent sprints in the first place).
- `run_eval.py:989`'s unconditional `for sprint_number in range(1, args.sprints + 1)` is the simplest
  starting point for a fix - it has no awareness of whether the previous iteration's sprint actually
  reached a clean boundary (`sprint_report_pending_release` False and a release merged) before advancing
  the label the manifest/report ultimately use.
- Worth checking in a follow-up run: whether this halves a 5-sprint run's *effective* planning cycles down
  to ~3 as a matter of course whenever a sprint's story count is large enough to fill 300 events, which
  would also mean the eval's `--sprints 5` budget is systematically overpromising distinct planning cycles.

## Test Approach
- `agents/scrum_team/tests/test_run_eval.py`: new test simulating a sprint whose scripted event stream
  completes all `advance_story_stage(..., "Accepted")` calls for its backlog but exhausts `max_events`
  before any `create_sprint_report` call; assert the manifest does not mark that sprint as ended-without-
  a-report in the dead-end sense, and that the following iteration's report (if it only finishes the prior
  close-out, confirmed by no intervening `start_sprint` call) is not presented as an independent sprint's
  own planning/delivery.
- Full `agents/scrum_team/tests` suite re-run to confirm no regression to SPEC-ISSUE-0056/0077's own tests.

## Resolution
- `agents/scrum_team/scripts/run_eval.py` (`_run_one_sprint`): snapshots the team's own `sprint_number`
  (bumped only by `start_sprint`, tools/scrum.py) at the start of the call, alongside the existing
  `sprint_report`/`kpi_update_count` baselines, and returns it as `team_sprint_number` plus
  `team_sprint_is_continuation` (`True` when `start_sprint` was never called this harness iteration - the
  carried-over state is still the previous real sprint's own close-out, not a new one).
- `final_halt_reason`'s computation gets a new `"host_cap"` value for `stop_reason in
  ("max_events_reached", "max_duration_exceeded")` without a critical halt - previously fell into the
  generic `"other"` bucket, whose own wording ("for a reason this harness didn't specifically distinguish")
  read like an undiagnosed failure for a sprint that had just finished 100% of its planned work.
- `agents/scrum_team/tools/budget.py` (`render_fallback_sprint_report`): new `reason == "host_cap"` branch -
  "## Mechanically-Rendered Report (Sprint's Turn Allowance Used in Full)" - stating plainly this is an
  expected way for a sprint to end, not a failure, and that it's the harness's own turn/time allowance,
  not this sprint's token/USD budget, that ran out.
- `agents/scrum_team/scripts/run_eval_analysis.py`:
  - `_FALLBACK_REPORT_MARKER` widened from the exact "Budget Used in Full" heading to the shared
    `"## Mechanically-Rendered Report ("` prefix every reason variant uses - the old exact-match missed
    the "Sprint Ended Without A Report"/"host_cap" headings entirely, so `0.1.0-run78`'s Sprint 1/Sprint 3
    both showed "Sprint Report? yes" despite being fallback stubs.
  - New `_real_sprint_label`/`_distinct_real_sprints_suffix` helpers surface `team_sprint_number` as a
    "Real Sprint #" column in the per-sprint metrics table (marking a continuation entry `"N (continued)"`)
    and as a parenthetical on the report header's "Sprints requested/completed" line, so a continuation
    entry is no longer presented as an independent sprint with its own planning/delivery. Both fall back
    to today's one-entry-per-sprint behavior for older manifests with no `team_sprint_number` data.
  - `_total_tokens_used` now sums only the LAST manifest entry per distinct `team_sprint_number` (that
    entry's `token_usage.total` already is the whole real sprint's cumulative figure) instead of summing
    every row unconditionally - the old version double-counted a continuation's carried-over total,
    reporting `0.1.0-run78`'s true ~16.8M distinct-sprint spend as 22.1M.
  - `_build_judge_prompt`'s per-sprint report listing and a new explanatory note tell the judge LLM
    directly that a repeated "real sprint #" marked `(continued)` means that iteration only finished
    writing up the previous iteration's already-completed work, not that the team did nothing that
    iteration.
- New/updated tests: `test_run_eval.py` (`TestHostSideSprintReportBackstop::test_backstop_passes_the_host_cap_reason_when_the_event_cap_was_hit_without_a_critical_halt`,
  new `TestRunOneSprintTeamSprintNumberTracking`), `test_budget.py`
  (`test_reason_host_cap_does_not_read_as_a_failure`), `test_run_eval_analysis.py` (new
  `test_fallback_marker_catches_every_reason_heading_not_just_budget`,
  `test_real_sprint_column_marks_a_continuation_of_the_previous_sprint`, new `TestTotalTokensUsed`, new
  `TestRenderReportSprintCountFraming`). Full `agents/scrum_team/tests` suite: 1174 passed, no regressions.
