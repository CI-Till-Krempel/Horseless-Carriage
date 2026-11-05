# Issue

- Issue ID: SPEC-ISSUE-0084
- Title: Differently-Worded Self-Referential Retro Issues Evade SPEC-ISSUE-0081's Recurrence Check And Livelock The Real Backlog
- Status: Done
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

**Structural gap underneath all of this: nothing ever shows the retro a list of what's already open.**
`add_retro_action`/`add_impediment` (`agents/scrum_team/tools/scrum.py:797,848`) take no
duplicate/reference parameter at all, and there is no tool anywhere in this codebase that lets a role
enumerate currently-open Issues before logging a new finding (confirmed: only `list_docs`, which lists
file paths, not status; `upsert_issue`/`upsert_backlog_item` are write-only). The only de-dup signal that
exists - `recurring_technical_finding_sprint` (`helpers.py:501-525`, invoked from
`_technical_category_nudge`, `scrum.py:704-751`) - runs automatically at log time but is purely
literal-keyword-based and, per the trace above, produces an advisory nudge the filing path doesn't
actually require anyone to act on. ScrumMaster is never put in a position to look at what's already
tracked and make an explicit call either way.

## Acceptance Criteria
- **Primary: make the duplicate-vs-new decision an explicit, visible part of logging a retro finding**,
  not a post-hoc text-similarity guess.
  - Add a way to enumerate currently-open Issues (status not in `_DONE_STATUSES`/not `Rejected`,
    `helpers.py:351,354`) from `product_backlog` - e.g. a `list_open_issues` tool - so a role logging a
    retro finding can actually see what's already filed before deciding.
  - `add_retro_action`/`add_impediment` gain a required field (when `category="technical"`) capturing that
    decision explicitly, e.g. `duplicate_of: str | None` plus a short `rationale` either way ("duplicate of
    SPEC-ISSUE-0003 because X" or "distinct from SPEC-ISSUE-0003 because Y").
  - If `duplicate_of` names a real, currently-open Issue: do **not** file/front-queue a new Issue at
    sprint-report time (`_file_retro_items_as_issues`, `budget.py:799-852`) - instead bump a recurrence
    counter on the *existing* Issue and feed it into SPEC-ISSUE-0081's existing 2+-recurrence
    steering-escalation threshold immediately, rather than waiting for yet another independent Issue to
    accumulate its own count.
- **Secondary: verify the self-declared decision, don't just trust it.** This project has already been
  burned once by relying on unverified model self-assessment for exactly this class of problem - SPEC-
  ISSUE-0081's own recurrence warning was advisory-only prose a cheap model simply proceeded past. When
  `duplicate_of` is left unset (claiming "genuinely new"), still run `recurring_technical_finding_sprint`
  (or an equivalent check) against currently-open Issues' text; if it fires despite the "new" claim, refuse
  the call and require either a corrected `duplicate_of` reference or an explicit, logged justification for
  why the lexical overlap is coincidental. This catches the literal-repeat case (SPEC-ISSUE-0081's original
  run79 evidence) even when the model's own judgment lapses; it will not by itself catch a genuinely
  differently-worded restatement that the model fails to recognize as a duplicate despite being shown the
  open-issues list - that's a model-quality gap this primary mechanism narrows but can't fully close.
- **Tertiary backstop: cap cumulative displacement regardless of how good the dedup is.** Even with the
  above, track displacement from the perspective of the story being pushed back: if the same real
  (non-Issue) backlog item has been displaced from "next in line" by auto-filed retro Issues for N
  consecutive sprints (N configurable, e.g. 2) without itself advancing a stage,
  `_file_retro_items_as_issues`/`_move_item_to_front_of_remaining_work` must stop auto-front-queuing
  further retro Issues ahead of it - a defense-in-depth guard against whatever slips past the first two
  layers (including a run of genuinely-distinct, legitimately-filed technical findings that still happen to
  starve the same story through bad luck).
- A regression test replaying this run's shape: log two retro findings in consecutive sprints with
  deliberately disjoint keyword sets, both nominally "technical," with the second one's `duplicate_of`
  correctly naming the first's filed Issue - assert no second Issue is created/front-queued.
- A regression test for the verification backstop: log a retro finding with `duplicate_of=None` whose text
  is near-identical (shares ≥2 keywords) to an already-open Issue - assert the call is refused rather than
  silently filing a duplicate.
- A regression test confirming a genuinely independent, one-off technical finding (SPEC-ISSUE-0076's own
  original motivating case, `duplicate_of=None`, no keyword overlap with any open Issue) still files and
  front-queues normally - the fix must not regress SPEC-ISSUE-0076's intended behavior for real, distinct
  findings.
- A regression test for the tertiary backstop: simulate N consecutive sprints each correctly filing a
  distinct, non-duplicate technical Issue that each legitimately front-queues ahead of the same still-
  `Ready` Must story - assert the N+1th stops front-queuing once the displacement threshold is reached.

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
- The primary mechanism deliberately shifts dedup from "guess by text similarity after the fact" to
  "require an explicit, visible decision at filing time" - a cheap model can still get that decision wrong
  for a subtle semantic duplicate, but it is no longer possible to file a duplicate *without ever having
  been shown what's already open*, which is the literal gap today (no listing tool exists at all).
- Worth checking in a follow-up run once fixed: whether the tertiary displacement cap has any unintended
  effect on SPEC-ISSUE-0076's original run76 scenario, where the front-queued Issue was a real,
  independently-resolvable technical blocker genuinely worth prioritizing over the next story.

## Test Approach
- Unit tests on `add_retro_action`/`add_impediment`: `duplicate_of` referencing a real open Issue skips
  filing a new one and increments the existing Issue's recurrence count instead; `duplicate_of=None` with
  no keyword overlap against open Issues files normally; `duplicate_of=None` WITH keyword overlap against
  an open Issue is refused.
- Unit test on the new `list_open_issues`-style helper: seed `product_backlog` with a mix of open/Accepted/
  Rejected Issues and assert only the genuinely-open ones are returned.
- Unit test on the tertiary displacement guard: seed a `Ready`, not-yet-`Accepted` Must story and simulate N
  consecutive sprints each filing a distinct, non-duplicate technical Issue naming/implying that story;
  assert the Nth+1 stops front-queuing.
- Integration-level: replay (or script-simulate) a `manifest.json`-shaped sequence matching this run's
  Sprint 2->Sprint 3 transition with the new `duplicate_of` field correctly set, and assert `US-0006` is not
  pushed back.

## Resolution
Implemented all three layers from the Acceptance Criteria, in priority order:

- **Primary - explicit duplicate-vs-new decision at filing time.** `list_open_issues`
  (`agents/scrum_team/tools/requirements.py`) enumerates currently-open `product_backlog` Issues (via new
  `is_issue_open`, `agents/scrum_team/helpers.py`) - the gap that made this impossible before (no tool
  anywhere could list them). `add_retro_action`/`add_impediment` (`agents/scrum_team/tools/scrum.py`) gain
  `duplicate_of`/`confirmed_distinct` params; a `duplicate_of` naming a real open Issue sets `issue_id`
  directly on the entry (reusing `_file_retro_items_as_issues`' existing "already has issue_id, don't
  refile" skip with zero changes to that function) and bumps the target Issue's new `recurrence_count` field
  via `bump_issue_recurrence` instead of letting a new Issue get filed/front-queued for the same thing.
- **Secondary - mechanical verification, not blind trust.** New `find_recurring_open_issue`
  (`helpers.py`) - like the existing `recurring_technical_finding_sprint` but scoped to entries whose
  filed Issue is still open, and returning the issue id, not just a sprint number. When `duplicate_of` is
  omitted, `_resolve_technical_duplicate` (`scrum.py`) runs this check and HARD-REFUSES the call if it
  fires - this project already learned the advisory-only-warning lesson once (SPEC-ISSUE-0081), so this
  is a real block, with `confirmed_distinct=True` as the explicit, logged override for a genuine
  coincidental overlap. An entry whose matching earlier finding's Issue already reached Accepted doesn't
  block - that complaint was genuinely resolved, so a fresh one sharing its keywords may be new work.
- **Tertiary - cumulative displacement cap.** `_move_item_to_front_of_remaining_work`
  (`requirements.py`) now tracks, via `_displacement_streak`/`_displacement_stage_count` on the nearest
  real (non-Issue) not-yet-Accepted item at or after the insert point, how many consecutive front-jumps
  have left it un-advanced; past `_MAX_CONSECUTIVE_DISPLACEMENTS` (2), a new Issue is inserted immediately
  *after* that item instead of before it. Keys on the nearest REAL item specifically (skipping over any
  Issues already stacked in front), not whichever item happens to sit at the insert point, so a chain of
  Issues piling up in front of one frozen story is still capped relative to that story.
- `agents/scrum_team/prompt_modules/ScrumMaster-workflow.md`'s RETROSPECTIVE REASONING section now
  instructs calling `list_open_issues` before filing a new `category="technical"` finding, matching this
  project's "enforce in code, reinforce in prompt" pattern - the code-level block is what actually matters,
  the prompt note is what makes the call-`list_open_issues`-first path the easy/obvious one to reach for.
- New/updated tests: `test_requirements.py` (`TestMoveItemToFrontOfRemainingWork` gains the displacement-cap
  cases; new `TestListOpenIssues`, `TestBumpIssueRecurrence`), `test_scrum.py` (new `duplicate_of`/
  `confirmed_distinct` cases on both `add_retro_action` and `add_impediment`), `test_budget.py` (new case
  confirming a `duplicate_of`-resolved entry isn't re-filed). Verified via a standalone script exercising
  the real functions directly (11 scenarios, all passing) plus a manual trace of every existing test in
  these three files against the new code paths - the Docker-based `agents/scrum_team/tests` suite could not
  be run in this environment (an unrelated `buildx` filesystem-entitlement prompt against this repo's
  named build context blocks the image build on this host); a human/CI run of the full suite is still
  the authoritative check.
