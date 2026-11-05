# Issue

- Issue ID: SPEC-ISSUE-0084
- Title: Differently-Worded Self-Referential Retro Issues Evade SPEC-ISSUE-0081's Recurrence Check And Livelock The Real Backlog
- Status: Ready
- Priority: Must
- Owner: Architect
- Last Updated: 2026-11-05

## Overview
Investigated `0.1.0-run84` (`gh run view 42043209866`, commit `0831d0e570d72a9686ba61ae29a3a25ba0d9df30`).
`report.md`'s Top Problem #1 (severity: high) named *"US-0006 (View Lists and Tasks UI) was left
unimplemented and unchecked in the product roadmap"*; Top Problem #2 named *"Duplicate and redundant
retrospective action items are repeatedly filed across sprints"*. These are the same root cause,
and it is a narrower-than-intended gap in SPEC-ISSUE-0081's own fix (Status: Done), not a new mechanism.

**Confirmed via `manifest.json`**: across all 5 sprint snapshots, `US-0006`'s `stages_completed` is frozen
at `['Draft', 'Ready']` from Sprint 1 through Sprint 5 - `start_feature_branch` was never once called with
`story_id: "US-0006"` anywhere in `transcript.md` (checked every `start_feature_branch` call in the full
transcript). Meanwhile `US-0001`-`US-0005` and `SPEC-ISSUE-0001`-`SPEC-ISSUE-0004` each reached `Accepted`
within the sprint they were planned.

**The ordering gate itself is working correctly.** `start_feature_branch`'s priority-order check
(`agents/scrum_team/tools/github.py:712-795`, via `_preceding_story` in
`agents/scrum_team/tools/requirements.py:1413-1477`) correctly blocked DevTeam from starting
`SPEC-ISSUE-0003` in Sprint 3 - `transcript.md:10539`: *"Cannot start work on 'SPEC-ISSUE-0003' - the
higher-priority story 'US-0006' must reach Accepted first. Development happens one story at a time, top to
bottom, in backlog priority order."* The gate reads whatever order `product_backlog` is actually in; it has
no bug.

**The actual mechanism: a chain of textually-different self-referential retro Issues each independently
front-jump ahead of US-0006.** `manifest.json`'s per-sprint `product_backlog` array shows the exact splice:
Sprint 2 order ends `..., US-0005[7], US-0006[8], SPEC-ISSUE-0003[9]`. Sprint 3's retrospective filed a new
Must-priority Issue (`add_retro_action`, `transcript.md` action text: *"Ensure all stories are successfully
planned and implemented before budget limits are reached"*) which became `SPEC-ISSUE-0004`
(`transcript.md:17549`), and Sprint 3's snapshot now reads `..., US-0005[7], SPEC-ISSUE-0004[8],
US-0006[9], SPEC-ISSUE-0003[10]` - `SPEC-ISSUE-0004` was spliced in at the exact array index `US-0006` used
to occupy, pushing it one slot later, via `_move_item_to_front_of_remaining_work`
(`agents/scrum_team/tools/requirements.py:336-374`, SPEC-ISSUE-0076's own fix, called from
`_file_retro_items_as_issues`, `agents/scrum_team/tools/budget.py:799-852`), which inserts immediately
before the first non-Epic, not-yet-`Accepted` backlog item.

**Why SPEC-ISSUE-0081's guard didn't catch this.** `budget.py:849-850` skips the front-jump only when
`entry.get("recurs_since_sprint") is not None` - a flag set by `recurring_technical_finding_sprint`
(`agents/scrum_team/helpers.py:501-525`), which requires the new retro finding's keyword set (tokens ≥5
chars, non-stopword, `helpers.py:486-498`) to share at least `_MIN_SHARED_KEYWORDS_FOR_RECURRENCE = 2`
(`helpers.py:485`) literal tokens with a prior "technical" retro entry. This is pure lexical-overlap
matching with no stemming. The Sprint 2 retro text ("Ensure continued alignment between Dev Team and QA on
API response types during story planning and test fixture setup" -> `SPEC-ISSUE-0003`) and the Sprint 3
retro text ("Ensure all stories are successfully planned and implemented before budget limits are
reached" -> `SPEC-ISSUE-0004`) share **zero** keywords under this scheme, despite both being, in substance,
restatements of "the team didn't finish its planned Must-priority work this sprint" - precisely the
self-referential category SPEC-ISSUE-0081's Acceptance Criteria asked to be detected and declined/defused.
`recurs_since_sprint` was never set, the `budget.py:849` guard never fired, and `SPEC-ISSUE-0004` front-
jumped exactly like a first-time, independently-resolvable technical finding would - which is the one case
SPEC-ISSUE-0076's mechanism is supposed to correctly prioritize.

**The result is cumulative, not a single bad call.** Each individual front-jump (SPEC-ISSUE-0003, then
SPEC-ISSUE-0004) is, in isolation, exactly what SPEC-ISSUE-0076 intended. But because a fresh,
differently-worded self-referential retro finding gets filed roughly every sprint, and each one re-splices
itself directly ahead of whatever is currently "first unimplemented" (which was US-0006 by Sprint 3), a
"Must"-priority product story that reached `Ready` in Sprint 1 never gets a single sprint where it is
actually first in line long enough for DevTeam to reach it - across all 5 sprints of this run.

## Acceptance Criteria
- The livelock guard must not key solely on text-similarity between successive retro findings
  (`recurring_technical_finding_sprint`'s keyword-overlap check). Instead, track displacement from the
  perspective of the story being pushed back: if the same real (non-Issue) backlog item has been displaced
  from "next in line" by one or more auto-filed retro Issues for N consecutive sprints (N configurable,
  e.g. 2) without itself advancing a stage, `_file_retro_items_as_issues`/`_move_item_to_front_of_remaining_work`
  must stop auto-front-queuing further retro Issues ahead of it - regardless of whether those Issues'
  wording overlaps with any prior one.
- A regression test replaying this run's shape: file two retro Issues in consecutive sprints with
  deliberately disjoint keyword sets (no shared ≥5-char non-stopword tokens), both nominally "technical"
  findings about unfinished Must work, both naming/implying the same still-`Ready` story as the reason -
  assert the second one does NOT get front-queued ahead of that story once the configured displacement
  threshold is reached.
- A second regression test confirms a genuinely independent, one-off technical finding (SPEC-ISSUE-0076's
  own original motivating case) still gets front-queued normally when no prior displacement has occurred -
  the fix must not regress SPEC-ISSUE-0076's intended behavior for real, distinct findings.

## Notes
- Distinct from SPEC-ISSUE-0076: that fix (the front-of-queue splice itself,
  `_move_item_to_front_of_remaining_work`) is confirmed working exactly as designed in this run - the gap
  is entirely in what's allowed to repeatedly invoke it.
- Distinct from SPEC-ISSUE-0081: that issue correctly identified this exact failure shape (its run79
  evidence: `US-0003`/`US-0004`/`US-0005` frozen at `['Draft','Ready']` while a chain of retro Issues got
  `Accepted` instead) and its Acceptance Criteria explicitly called for detecting "a retro action/
  impediment [that] is reflexively about existing unfinished Must work" - but the shipped fix
  (`c70b569`, PR #477) implemented only a literal keyword-overlap recurrence check
  (`_MIN_SHARED_KEYWORDS_FOR_RECURRENCE = 2`, `helpers.py:485`), which is narrower than the acceptance
  criteria's own framing and does not generalize to semantically-similar-but-differently-worded findings.
  This issue exists because that gap was never closed, not because the mechanism broke.
- `run84`'s target repo (`CI-Till-Krempel/horseless-carriage-eval-todo-app`, eval branch
  `eval/0.1.0-run84/*`) and its auto-filed `SPEC-ISSUE-0001` through `SPEC-ISSUE-0005` are evidence
  artifacts of this run, not real tracked issues in this codebase - referenced here only as the data this
  investigation is based on.
- Worth checking in a follow-up run once fixed: whether capping displacement by story (rather than by
  retro-text similarity) has any unintended effect on SPEC-ISSUE-0076's original run76 scenario, where the
  front-queued Issue was a real, independently-resolvable technical blocker genuinely worth prioritizing
  over the next story.

## Test Approach
- Unit test on `_file_retro_items_as_issues`/`_move_item_to_front_of_remaining_work` (or wherever the new
  displacement-count guard lives): seed `product_backlog` with a `Ready`, not-yet-`Accepted` Must story and
  simulate N consecutive sprints each auto-filing a textually-disjoint "technical" retro Issue naming/
  implying that story; assert the Nth+1 Issue is not spliced ahead of it.
- Unit test confirming a single, first-time technical finding (no prior displacement recorded) still
  front-queues normally - guards against over-correcting into never front-queuing legitimate blockers.
- Integration-level: replay (or script-simulate) a `manifest.json`-shaped sequence matching this run's
  Sprint 2->Sprint 3 transition and assert `US-0006` is not pushed back by the simulated `SPEC-ISSUE-0004`
  filing once the displacement threshold is reached.
