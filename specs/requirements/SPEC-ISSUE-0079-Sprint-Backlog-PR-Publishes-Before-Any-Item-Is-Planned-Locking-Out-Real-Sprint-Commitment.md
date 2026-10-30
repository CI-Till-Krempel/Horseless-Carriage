# Issue

- Issue ID: SPEC-ISSUE-0079
- Title: Sprint Backlog PR Publishes Before Any Item Is Planned, Locking plan_sprint_backlog_item Behind A Fragile Override Every Single Sprint
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-30

## Overview
`0.1.0-run77`'s own judge report (`report.md`, Top Problem #2, severity: high) flagged the team as
"trapped in circular process overhead ... rather than feature delivery": across all 5 sprints, only 2 of
6 real stories (US-0001, US-0002) and 3 retro-filed Issues ever got a `start_feature_branch` call - the
Must-priority US-0003 through US-0006 sat `Ready` the entire run, never attempted even once, despite
every sprint closing with 1.3-3.1M of its ~5M token budget unused (`manifest.json`: sprint token totals
2,873,168 / 3,076,674 / 2,361,638 / 1,339,993 / 1,623,805, each well under `budgets.total`).
`manifest.json` confirms every single sprint's `sprint_backlog` ended up with **exactly one** item -
sprint 1 `['US-0001']`, sprint 2 `['SPEC-ISSUE-0001']`, sprint 3 `['US-0002']`, sprint 4
`['SPEC-ISSUE-0002']`, sprint 5 `['SPEC-ISSUE-0003']` - and `grep -n '"status": "error"'` plus a search
for the ordering-gate's own rejection text in `transcript.md` found **zero** ordering-gate rejections:
the real stories weren't mechanically blocked from being planned, DevTeam simply never got a second item
into `sprint_backlog` within any sprint.

**Confirmed mechanism.** `plan_sprint_backlog_item` (`agents/scrum_team/tools/scrum.py:1018`, the only
tool that writes an item into `sprint_backlog`, DevTeam-exclusive) was phase-gated to ONLY
`"Plan & Start Sprint"` (`agents/scrum_team/helpers.py:827-832`, `PHASE_TOOL_POLICY`) - it was absent
from the `"Development"` bucket entirely. But Product Owner's own prompt
(`agents/scrum_team/prompt_modules/ProductOwner-workflow.md:69-80`, "SPRINT PLANNING - PUBLISH THE
BACKLOG BEFORE DEV TEAM STARTS") - and `current_sprint_phase` (`helpers.py:887-915`, which flips the
phase to `"Development"` the instant `create_sprint_backlog_pr`'s PR merges, with no check on whether
`sprint_backlog` holds anything yet) - together mean Product Owner always publishes that PR, locking the
phase into `"Development"`, **before** Dev Team has ever had a turn to call `plan_sprint_backlog_item`.
This is also the system's own already-tested design intent, not a misuse of it: `test_github.py`'s
`TestCreateSprintBacklogPr` already asserts the PR merges successfully with `sprint_backlog` empty (or
holding only the prior sprint's now-Accepted items, pruned to empty as part of that same call) - the
expectation has always been that `sprint_backlog` gets populated *after* this PR merges, during
Development.

With `plan_sprint_backlog_item` missing from `PHASE_TOOL_POLICY["Development"]`, every sprint had to
fight through the tool's own "immediate identical retry" override escape hatch
(`agents/scrum_team/agent.py:2426-2428`) just to plan its *first* item. That escape hatch requires the
retry's arguments to compare equal to the immediately-preceding rejected call - fragile against an LLM's
natural non-determinism in reproducing a large dict verbatim. `transcript.md` sprint 2 (events 37-70)
shows this exactly: attempt 1 has a typo'd key (`"タsks"` instead of `"tasks"`, rejected), attempt 2
drops the typo but is missing `test_approach` versus attempt 1 (rejected again), DevTeam detours through
`start_feature_branch` failures, a `transfer_to_agent` loop-detector trip (event 52), a blocked-story
dead end needing Architect to intervene (events 53-64), before attempt 3/4 finally reproduce an identical
payload and succeed (event 70) - roughly 30 wasted tool-call events and a meaningful fraction of that
sprint's token budget spent fighting the lockout for the *one* item that did get planned. Planning a
second item would cost the same again; no sprint in this run ever attempted it.

This is not "agent behavior under a working guardrail" (Step 4 of the investigation skill): the gate's
own phase-tool policy has a real logic gap (the one tool that must run during Development to make any
progress was never actually listed as allowed there) - there is no path under which normal play avoids
the override every single sprint.

## Acceptance Criteria
- `plan_sprint_backlog_item` must be callable during `"Development"` phase, not just
  `"Plan & Start Sprint"`, without needing the two-call "exact same arguments" override.
- The `"Development"` entry in `SPRINT_PHASE_GUIDANCE` (`helpers.py`) must stop telling the team that
  "re-planning belongs in Conceptual Work/Plan & Start Sprint, not here" for this tool specifically -
  committing an additional Ready story into an active sprint is routine Development-phase work.
- A new regression test proving `plan_sprint_backlog_item` raises no phase violation when
  `current_sprint_phase` is `"Development"` (mirroring the existing `write_file`/`advance_story_stage`
  Development-allowed cases in `TestDetectPhaseViolation`), while still correctly blocking it during
  `"Conceptual Work"`.
- Must not regress `create_sprint_backlog_pr`'s existing `unaddressed_must_issues`/
  `ready_backlog_shortfall` gates, nor the override escape hatch itself (still the right mechanism for
  genuinely out-of-policy tools it continues to gate).

## Notes
- **Why not gate `create_sprint_backlog_pr` on a non-empty `sprint_backlog` instead** (the fix shape
  originally proposed when this issue was filed): `agents/scrum_team/tests/test_github.py`'s own
  `TestCreateSprintBacklogPr` already asserts the opposite - `test_happy_path_opens_and_merges_against_develop`
  and `test_snapshots_engagement_baseline_on_the_first_merge_this_sprint` both merge successfully with no
  `sprint_backlog` at all in state, and `test_prunes_a_prior_sprints_accepted_items_on_the_first_merge_this_sprint`
  explicitly merges a sprint whose `sprint_backlog` only holds the *prior* sprint's now-Accepted items,
  which get pruned to empty as part of that same successful call. The system's own, already-tested design
  intent is that `sprint_backlog` starts empty each sprint and gets populated via `plan_sprint_backlog_item`
  calls made *during* Development - the real defect was that `PHASE_TOOL_POLICY`'s `"Development"` entry
  never actually allowed that tool there, so every real run had to pay for the override on literally the
  sprint's first (and, in practice, only) item.
- Distinct from SPEC-ISSUE-0076 (also run76/77-adjacent): that issue fixed a retro-filed Issue's backlog
  *position* so the ordering gate could see it; this issue is about `plan_sprint_backlog_item` itself
  being phase-blocked during the phase it actually runs in.
- The override mechanism itself (`agent.py:2426-2428`) remains sound as a last-resort escape hatch for
  genuinely out-of-policy tools; the bug was that `plan_sprint_backlog_item` was never correctly bucketed
  as Development-phase work in the first place, making the override its *only* path every single sprint.
- `report.md`'s framing ("circular process overhead") is a judge-LLM's surface read; the actual mechanism
  is a one-line `PHASE_TOOL_POLICY` omission paid for on every sprint's first planning call, not the team
  "getting distracted" by SPEC-ISSUE busywork - SPEC-ISSUE-0001-0003 getting worked at all was itself only
  possible because the override happened to eventually succeed.

## Resolution
Added `"plan_sprint_backlog_item"` to `PHASE_TOOL_POLICY["Development"]`
(`agents/scrum_team/helpers.py`) alongside its existing `"Plan & Start Sprint"` membership - it is now
allowed in both phases instead of only the one that, by construction, Dev Team never gets a turn in
before Product Owner's `create_sprint_backlog_pr` has already locked the phase into Development. Reworded
`SPRINT_PHASE_GUIDANCE["Development"]` to say committing an additional Ready story into the active
sprint is fine there, while still steering genuinely new backlog content (`upsert_story`/`upsert_epic`/
etc.) back to the earlier phases. Added a line to `DevTeam-workflow.md`'s ESTIMATION section pointing out
that `plan_sprint_backlog_item` now works normally throughout Development, tying it to
`sprint_capacity_advisory`'s existing "pull more Ready stories into this sprint" suggestion so the advice
is actually actionable without friction.

Tests added (`agents/scrum_team/tests/test_agent.py`, `TestDetectPhaseViolation`):
`test_allows_plan_sprint_backlog_item_during_development`,
`test_allows_plan_sprint_backlog_item_during_plan_and_start_sprint`,
`test_blocks_plan_sprint_backlog_item_during_conceptual_work`.

Full suite (`agents/scrum_team/tests`, via `docker compose run`) passes 1160/1160, including every
existing `TestCreateSprintBacklogPr` test unchanged - confirming the alternative fix shape considered
above (gating the PR merge itself) would have been the wrong, regressive direction.

## Test Approach
- `agents/scrum_team/tests/test_agent.py`: `TestDetectPhaseViolation` - `plan_sprint_backlog_item` is
  allowed during `"Development"` and `"Plan & Start Sprint"`, still blocked during `"Conceptual Work"`.
- Manual: grep the next eval run's `transcript.md` for the override-retry pattern seen in run77
  (events 37-70, a typo'd/incomplete payload rejected twice before an identical retry finally got
  through) to confirm it no longer fires on a sprint's first planned item.
