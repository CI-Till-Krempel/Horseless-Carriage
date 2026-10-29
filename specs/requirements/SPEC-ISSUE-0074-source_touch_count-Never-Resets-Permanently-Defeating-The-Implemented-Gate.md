# Issue

- Issue ID: SPEC-ISSUE-0074
- Title: `sprint_files_touched` Dedupes By Path For The Whole Run, Not Per-Story - Permanently Defeating `advance_story_stage`'s "Real Code Was Written" Gate Once A Project's File Set Plateaus
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-29

## Overview
Investigated `0.1.0-run76` (completed all 5 requested sprints, but `report.md` flagged "unused and dead
artifact files" - `app_us0006.py`, containing only `print("US-0006 implementation file update")` -
clutter nearly identical to what SPEC-ISSUE-0046's Finding 3 already diagnosed and supposedly fixed).

**Confirmed mechanism - this is a reopened regression of SPEC-ISSUE-0046 Finding 3, not a new class of
bug.** `advance_story_stage`'s Implemented gate (`agents/scrum_team/tools/requirements.py:1729-1758`)
rejects the transition unless `source_touch_count > dev_touch_baseline`, where `source_touch_count =
sum(1 for f in touched if is_source_file(f))` and `touched = state["sprint_files_touched"]`
(`requirements.py:1730-1732`). The bug is in what populates `sprint_files_touched`:
`_record_touched_file` (`agents/scrum_team/tools/base.py:27-38`) appends a path only `if rel_path not in
touched` - a **whole-run, never-reset deduplication by path**, despite the field being named
`sprint_files_touched` and appearing in `REPO_STATE_KEYS` (`agents/scrum_team/tools/scrum.py:58`) with
no reset call anywhere in the codebase (`grep -rn "sprint_files_touched"` across `agents/scrum_team/`
shows only the one append site, one `setdefault`, and two readers - never a `.clear()` or reassignment
to `[]` at a sprint boundary).

For a small app with a stable, small set of source paths (here: `app.py`, `requirements.txt`,
`templates/index.html`, `tests/test_app.py` - the overwhelmingly common shape for *any* real project
past its first few files), `source_touch_count` necessarily **plateaus** at that fixed path count the
first time all of them have been touched once (Sprint 1, US-0001:
`transcript.md:2076` - all four paths created in one commit). `dev_touch_baseline` is set to match
`source_touch_count` at every successful Implemented transition (`requirements.py:2014-2015`), so from
that point on `source_touch_count <= dev_touch_baseline` is true **by construction**, for any later
story whose real code changes land in already-touched paths - which is guaranteed for a project this
size, since there are no new paths left to introduce. The gate cannot distinguish "nothing new was
written" from "the same four files got legitimately rewritten again" - it only ever asks "how many
*distinct paths*, cumulatively, across the entire run."

**Confirmed by repetition, not a one-off:** this exact rejection -
`"Cannot mark '<ID>' Implemented - no real source file has been written via write_file since the last
story was Implemented"` - fired for US-0002 (`transcript.md:5537`, `5758`), US-0004
(`transcript.md:10774`, `10962`), US-0005 (`transcript.md:12213`), and US-0006 three times
(`transcript.md:15971`, `16145`, and a third that tripped the 3-strikes loop breaker,
`transcript.md:16320`) - **every story after the very first one of the run**, each time DevTeam had in
fact just written real, substantively different code to `app.py`/`tests/test_app.py` moments earlier
(e.g. US-0006's rewrite at `transcript.md:15727` - a full `app.py` overwrite, confirmed real and
different from every prior version; see SPEC-ISSUE-0075 for that rewrite's own separate, severe
consequences).

**The SPEC-ISSUE-0046 escape hatch (`implemented_via_earlier_work`) is itself undermined by the same
bug.** For US-0006, DevTeam's first attempt to supply `implemented_via_earlier_work` honestly
(`transcript.md:16255`: `"Code was written via write_file in app.py and tests/test_app.py in this
branch for US-0006."`) was rejected outright - not because the justification was weak, but because by
then the story had already been marked BLOCKED by the 3-strikes loop breaker
(`transcript.md:16320-16339`), which `advance_story_stage` refuses regardless of arguments until
`resolve_story_blocker` runs first. After Architect unblocked it (`transcript.md` events 67-70), the
exact same rejection fired again (`transcript.md:16145`'s message recurs), and DevTeam gave up and
fabricated `app_us0006.py` (`transcript.md:16577-16671`) - the identical anti-pattern SPEC-ISSUE-0046
was filed specifically to eliminate ("a real eval run's app.py already covered several closely-related
stories' logic... fabricated a throwaway one-line 'verification' stub file 4 times in a row"). The
underlying detector SPEC-ISSUE-0046 left in place is still capable of triggering the exact failure mode
its own fix was meant to end.

**This also silently defeats a second consumer of the same signal.** `_detect_unadvanced_implementation`
(`agents/scrum_team/agent.py:2146-2184`) reuses `source_touch_count`/`dev_touch_baseline` to nudge
DevTeam when it transfers away with real, un-advanced work still sitting in a story - its own gate
(`agent.py:2183`: `if source_touch_count <= dev_touch_baseline ... return None`) is permanently
satisfied (never fires) by this same plateau, for the identical reason.

## Acceptance Criteria
- `advance_story_stage`'s Implemented gate must distinguish "a path was touched at some point earlier
  in the run" from "this path was touched again, with new content, since the last Implemented
  transition" - re-touching an already-known path with a genuinely new `write_file` call must count as
  real progress toward this gate, not be silently absorbed into a cumulative dedup set.
- A story whose `write_file` calls only touch paths already present in `sprint_files_touched` from a
  *prior* story's work (same sprint or an earlier one) must still successfully advance to Implemented,
  provided the file's actual content changed since the last Implemented transition - no story should
  ever be forced into `implemented_via_earlier_work`/a fabricated placeholder file merely because the
  project's total distinct source-path count has plateaued.
- `_detect_unadvanced_implementation`'s nudge (`agent.py:2146-2184`) must regain the ability to fire
  after the fix - verify it isn't permanently defeated the same way.
- Does not regress SPEC-ISSUE-0046's actual intent: a story with genuinely nothing new written (true
  "already covered by earlier work in this same pass") must still require `implemented_via_earlier_work`
  or `{"spike": true}` - this fix narrows the false-positive window, it doesn't remove the check.
- A new regression test replaying this run's shape: `write_file("app.py", ...)` for story A (succeeds,
  bumps `dev_touch_baseline`), then a *different*, genuinely new `write_file("app.py", ...)` for story B
  (same path, materially different content) - `advance_story_stage(B, "Implemented")` must succeed
  without requiring `implemented_via_earlier_work`.

## Notes
- Root cause lives in `_record_touched_file` (`agents/scrum_team/tools/base.py:27-38`) - the
  `if rel_path not in touched` dedup is the single point where "touched at all, ever" gets conflated
  with "touched recently enough to count." A content-hash-per-path comparison (only append/replace when
  the file's hash actually changed since its last recorded touch) or a per-Implemented-transition reset
  of `sprint_files_touched` (consistent with its own name) would both close this without reopening
  SPEC-ISSUE-0046's original problem (fabricated junk files when truly nothing changed).
- This is the second time this exact check class has caused real harm (SPEC-ISSUE-0046 Finding 3, now
  this) - the escape hatch added last time treated the *symptom* (nowhere honest to say "already
  covered"), not the detector's own over-triggering, which is why it recurred.
- See SPEC-ISSUE-0075 for a related, independently-confirmed finding from the same run: DevTeam's
  blind full-file rewrite of `app.py` for US-0006, which is what actually produced the "new" content
  that tripped this gate in the first place - a different root cause, filed separately since fixing this
  gate alone would not have prevented that regression.

## Test Approach
- `agents/scrum_team/tests/test_requirements.py` (or wherever `advance_story_stage`'s Implemented gate
  is tested): construct state where `sprint_files_touched` already contains `app.py` (from an earlier
  successful Implemented transition, `dev_touch_baseline` set accordingly), then simulate a fresh
  `write_file("app.py", <different content>)` call for a new story and assert
  `advance_story_stage(new_story, "Implemented")` succeeds without `implemented_via_earlier_work`.
- Add the SPEC-ISSUE-0046 regression case alongside it (no new `write_file` at all since the last
  Implemented transition) and assert the gate still correctly rejects without a justification - proving
  the fix narrows, rather than removes, the check.
- `agents/scrum_team/tests/test_agent.py` (`_detect_unadvanced_implementation`): add a case confirming
  the nudge still fires after a plateau is reached and real new work lands in an already-touched path.

## Resolution
Added `source_content_change_count` - a new, separate, never-deduped-by-path counter
(`agents/scrum_team/tools/base.py`'s `_record_source_content_change`) bumped by `write_file`/
`delete_file` (`agents/scrum_team/tools/docs.py`) whenever a non-spec (`is_source_file`) path's content
actually changes (new file, or an overwrite whose content differs) or is deleted. Deliberately kept
separate from `sprint_files_touched`/`_record_touched_file`, which keeps its existing whole-run,
deduplicated-by-path behavior unchanged - that signal has its own, different consumer (verifying every
touched path landed in a release) whose tests (`test_scrum.py`, `test_docs.py`) depend on the dedup.

`advance_story_stage`'s Implemented gate (`agents/scrum_team/tools/requirements.py`) and
`_detect_unadvanced_implementation` (`agents/scrum_team/agent.py`) both now read
`source_content_change_count` instead of deriving a distinct-path count from `sprint_files_touched` -
the `dev_touch_baseline`/`unadvanced_write_nudge_baseline` comparison logic itself is unchanged, only
what it's compared against.

Tests added:
- `agents/scrum_team/tests/test_docs.py`: `test_write_file_increments_source_content_change_count_for_a_new_source_file`,
  `test_write_file_increments_source_content_change_count_on_each_genuine_rewrite_of_the_same_path` (the
  direct regression test - two rewrites of the same path both count, while `sprint_files_touched` stays
  deduped at one entry), `test_write_file_does_not_increment_source_content_change_count_for_identical_overwrite`,
  `test_write_file_does_not_increment_source_content_change_count_for_a_spec_path`,
  `test_delete_file_increments_source_content_change_count`,
  `test_delete_file_does_not_increment_source_content_change_count_for_a_spec_path`.
- `agents/scrum_team/tests/test_requirements.py`: `test_implemented_succeeds_when_a_later_story_rewrites_an_already_known_path` -
  replays the exact plateau shape (`dev_touch_baseline` already non-zero from an earlier story,
  further genuine writes to already-known paths) and confirms the gate now succeeds.
- Updated every existing test in `test_requirements.py`/`test_agent.py` that previously simulated "real
  source work happened" via `sprint_files_touched` to use `source_content_change_count` instead (the
  three tests checking "only a spec file touched" were left at the default 0, correctly still rejecting).

`agents/scrum_team/tests/{test_docs,test_requirements,test_agent,test_scrum,test_state_persistence}.py`
(via `docker compose run`, project-scoped per `lib_docker.compose_project_args("test")`): all pass
except the one pre-existing, unrelated failure already tracked (`test_agent.py`'s identity-fallback
test).
