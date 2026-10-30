# Issue

- Issue ID: SPEC-ISSUE-0081
- Title: Auto-Filed Retro Issues That Are Themselves About The Team's Own Unfinished Must Stories Livelock The Real Backlog - SPEC-ISSUE-0076's Ordering Fix Now Blocks On A Complaint The Team Can Satisfy With Zero Code
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-30

## Overview
Investigated `0.1.0-run79` (`gh run view 41036038566`, commit `918bd45976a1092ce877a400c27b8ebf7da4d6a2`).
`report.md` scored Team Efficiency **1/5** and named, as Top Problems #1/#2: *"Core MVP user stories
US-0003, US-0004, and US-0005 were left uncompleted and abandoned in backlog"* and *"Excessive token and
sprint budget consumed on internal process meta-issues instead of product features"* - the team delivered
2 of 5 stories in Sprint 1, then produced **zero further product code for the remaining four sprints**
(confirmed: `grep -c '"path": "app.py"\|"path": "test_app.py"'` against `transcript.md`'s Sprint 2/3/4/5
sections each returns `0`).

**Confirmed mechanism - a livelock, not a one-off bad retro.** Sprint 1 correctly finished US-0001/US-0002
and its retrospective correctly noted the shortfall: ScrumMaster logged `add_retro_action` ("Ensure all
planned stories are fully developed and accepted within sprint timebox or cleanly scoped.") and
`add_impediment` ("Sprint budget or iteration boundary reached with US-0003, US-0004, US-0005
remaining.") - both `category: "technical"` (`transcript.md:3956-3997`). `_file_retro_items_as_issues`
(`agents/scrum_team/tools/budget.py:765-826`) auto-filed both as Must-priority Issues (SPEC-ISSUE-0001,
SPEC-ISSUE-0002) and, per SPEC-ISSUE-0076's own fix, moved each to the front of the one-story-at-a-time
ordering queue via `_move_item_to_front_of_remaining_work` - working exactly as that fix intended.

Sprint 2's ProductOwner tried to plan the real remaining work and was mechanically refused:
`create_sprint_backlog_pr`'s rejection (`transcript.md:4687`, condensed) reads *"Cannot create the Sprint
Backlog PR yet - 2 Must-priority Issue(s) filed from a previous sprint's retrospective/impediment log are
still sitting unaddressed at Draft ... a retro-filed Must-priority Issue is already mechanically positioned
at the front of the one-story-at-a-time ordering queue (SPEC-ISSUE-0076), blocking Dev Team from starting
any other work until it's genuinely resolved."* DevTeam then planned SPEC-ISSUE-0001 with this exact
approach (`transcript.md:5454-5466`): *"Acknowledge and document process flow for ensuring planned stories
are properly tracked and scoped within sprint boundaries ... No code changes required for this
administrative/process issue."* `start_feature_branch("SPEC-ISSUE-0001")` was itself refused first
(`transcript.md:5528-5535`) until SPEC-ISSUE-0002 reached `Accepted` - *"Development happens one story at a
time, top to bottom ... resolve it for real ... rather than reaching for set_priority(...) to dodge it."*
Both Issues were fully "resolved" this way - with the plan's own words confirming zero real work - and
US-0003/US-0004/US-0005 never got a single `plan_sprint_backlog_item`/`start_feature_branch` call in
Sprint 2 at all.

**The cycle then repeats three more times, each a near-duplicate of the last.** Sprint 2's own retrospective
(`transcript.md:7679-7696`) logged *"Ensure stories and issues are planned accurately with realistic
estimates and tracked cleanly through the workflow stages"* -> auto-filed as SPEC-ISSUE-0003, again jumping
the queue. Sprint 3's retrospective (`transcript.md:10376-10385`) logged *"Maintain disciplined backlog
planning and ensure all issues are tracked cleanly through workflow stages"* -> SPEC-ISSUE-0004. The
pattern continues through SPEC-ISSUE-0005 (Sprint 4) and SPEC-ISSUE-0006 (Sprint 5). `manifest.json`
confirms the result directly: across all 5 sprint snapshots, US-0003/US-0004/US-0005's `stages_completed`
is frozen at exactly `['Draft', 'Ready']` from Sprint 1 through Sprint 5 - never `Implemented` - while
SPEC-ISSUE-0001 through SPEC-ISSUE-0005 each reach `['Draft', 'Ready', 'Implemented', 'Reviewed', 'Tested',
'Accepted']` within the very same sprint they were filed.

**Why this is a livelock, not just a recurring bad retro (distinguishing from SPEC-ISSUE-0076).**
SPEC-ISSUE-0076 fixed the mechanism that moves a freshly-filed Must Issue to the front of the queue - it
now works perfectly, confirmed blocking exactly as designed in this run. The gap is one level up: nothing
in `_file_retro_items_as_issues` distinguishes a genuinely new, independently-resolvable technical finding
(SPEC-ISSUE-0076's own run76 example - a real QA/test-integration pain point, fixable without touching the
blocked stories) from a finding that is *itself just a restatement of the team's own failure to finish
already-queued Must-priority stories*. The second kind is circular by construction: the only real way to
"resolve" it is to finish those very stories, but DevTeam instead satisfies it with documentation-only
"process" work (its own plan says so explicitly), which guarantees the underlying shortfall persists,
which guarantees the next sprint's retrospective rediscovers the identical problem and files another
Issue that again jumps the queue ahead of the stories it's nominally about. The ordering gate and the
auto-filing mechanism are each individually working as designed; together, for this specific class of
self-referential finding, they produce a run that can never make further product progress once the first
one fires.

## Acceptance Criteria
- `_file_retro_items_as_issues` (or the auto-prioritization/front-of-queue step it calls) must not let a
  newly-filed Must Issue indefinitely displace the already-queued Must-priority stories it is substantively
  about not being finished - either detect that a retro action/impediment is reflexively about existing
  unfinished Must work (rather than a distinct, independently-resolvable technical defect) and decline to
  auto-file/front-queue it as a *new* blocker, or otherwise ensure that resolving it cannot be satisfied
  without real progress on the stories it names.
- A regression test replaying this run's shape: a retro item citing specific already-`Ready`,
  not-yet-`Accepted` Must-priority story IDs as "remaining"/"unfinished" must not result in an auto-filed
  Issue that out-ranks those same stories in the ordering queue.
- A second regression test simulating several sprints of this exact pattern (each sprint's retrospective
  re-filing a near-identical "finish your planned work" finding) - assert the real Must stories make
  measurable stage progress within a bounded number of sprints, rather than staying frozen at `['Draft',
  'Ready']` indefinitely while a chain of paperwork-only Issues gets Accepted instead.

## Notes
- Distinct from SPEC-ISSUE-0076: that issue's own fix (`_move_item_to_front_of_remaining_work`) is
  confirmed working correctly in this run (`transcript.md:4687`, `5528-5535` both name it and behave
  exactly as designed) - this issue is about what gets fed into that mechanism, not the mechanism itself.
- Also distinct from "ScrumMaster/ProductOwner having a bad retro" (Step 4 of the investigation process):
  the *content* of each individual retro finding is reasonable on its own (every sprint genuinely did end
  with unfinished Must work) - the gap is structural, in how an auto-filed Issue about that fact gets
  treated identically to any other independently-resolvable technical finding, with no mechanism stopping
  it from being satisfied by documentation alone while the real complaint persists.
- `run79`'s target repo (`CI-Till-Krempel/horseless-carriage-eval-todo-app`, eval branch
  `eval/0.1.0-run79/*`) and its auto-filed SPEC-ISSUE-0001 through SPEC-ISSUE-0006 are evidence artifacts
  of this run, not real tracked issues in this codebase - referenced here only as the data this
  investigation is based on.
- Worth a follow-up check in a later run: whether `advance_story_stage`'s own "real code written" gate
  (SPEC-ISSUE-0074's territory) could independently help here by refusing to accept a process-only Issue
  with zero source touches when a Must-priority story is still open - a different, complementary angle
  from gating what gets auto-filed/positioned in the first place.

## Test Approach
- `agents/scrum_team/tests/test_budget.py` (`TestFileRetroItemsAsIssues`): a retro action/impediment whose
  text names already-queued, not-yet-Accepted Must-priority story IDs as the "remaining"/"unfinished" work
  must not result in the auto-filed Issue being positioned ahead of those stories.
- `agents/scrum_team/tests/test_github.py`: end-to-end - file such a retro item through the real function,
  then confirm `start_feature_branch` on one of the named real stories is NOT refused by the synthetic
  Issue the way `0.1.0-run79`'s Sprint 2 showed.
- A multi-sprint simulation test (new, likely in `test_run_eval.py` or a dedicated scenario test) replaying
  several rounds of "retro re-files a near-identical finding" and asserting real story progress isn't
  permanently frozen.

## Resolution
Reused the existing GH #354 recurrence-detection mechanism (`recurring_technical_finding_sprint`,
`agents/scrum_team/helpers.py`) instead of inventing new text-similarity detection - it already computed
the exact right answer in `0.1.0-run79` (the warning text literally said *"A similar finding was already
logged as 'technical' back in sprint 1 with no resolution since"*), it just had no teeth: the warning was
advisory-only prose in a tool response, and a cheap model simply proceeded past it unchanged every time.

- `agents/scrum_team/tools/scrum.py` (`_technical_category_nudge`): now returns `(warning,
  recurs_since_sprint)` instead of just the warning string - `add_retro_action`/`add_impediment` persist
  `recurs_since_sprint` directly on the logged entry (not just rendered into prose), so later code can act
  on it mechanically rather than re-deriving it.
- `agents/scrum_team/tools/budget.py` (`_file_retro_items_as_issues`): an entry flagged
  `recurs_since_sprint` still gets filed and Must-prioritized as before, but is no longer passed to
  `_move_item_to_front_of_remaining_work` (SPEC-ISSUE-0076's own queue-jump helper) - it ranks via the
  regular stable sort instead, so it can never again cut in front of the already-queued, already-Must
  stories it's a restatement of complaining about. A genuinely new (non-recurring) finding is completely
  unaffected - SPEC-ISSUE-0076's original behavior is preserved for it.
- `agents/scrum_team/tools/budget.py` (`create_sprint_report`'s steering-escalation backstop, GH #381):
  broadened from counting only `looks_like_role_behavior_finding`-phrase-matched entries to also counting
  any entry flagged `recurs_since_sprint` - `0.1.0-run79`'s actual wording ("Ensure all planned stories are
  fully developed and accepted within sprint timebox...") matched none of the phrase list, so this backstop
  never fired despite 4 consecutive sprints of the identical complaint recurring. Now 2+ such findings
  (either signal) force a real `propose_steering_change` call before the sprint report can close, instead
  of allowing an unbounded chain of near-duplicate "technical" Issues.
- New/updated tests: `test_scrum.py` (`test_recurring_impediment_persists_recurs_since_sprint_on_the_entry`,
  `test_a_genuinely_new_technical_finding_has_no_recurs_since_sprint`), `test_budget.py`
  (`test_a_confirmed_recurring_finding_does_not_jump_ahead_of_the_real_backlog`,
  `test_a_non_recurring_finding_still_jumps_ahead_as_before`,
  `test_create_sprint_report_rejects_two_confirmed_recurring_findings_even_without_role_behavior_wording`).
  Full `agents/scrum_team/tests` suite: 1179 passed, no regressions.
