# Issue

- Issue ID: SPEC-ISSUE-0071
- Title: `advance_story_stage`'s Rejection Message Blames "Roadmap Sync" Even When Only The Story Markdown (DoR Content) Check Failed
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-10-27

## Overview
Reported while investigating `0.1.0-run73`: the maintainer flagged "more errors about... syncing the
roadmap" while reading the run's logs. Grepping `transcript.md` for every occurrence (US-0001 through
US-0006, and later `SPEC-ISSUE-0001`, e.g. `transcript.md:796,1047,1177,1307,1437,1567,3865,5797`)
shows the same message every time:

> `'<id>' is recorded as Ready in state, but syncing specs/ROADMAP.md and/or the story file failed -
> see story_markdown/roadmap_sync for details. Retry so the roadmap actually reflects this.`

**Confirmed: `specs/ROADMAP.md` sync never actually failed in any of these occurrences.** Each
response's own `roadmap_sync` sub-field reports `{"status": "ok", "message": "Roadmap updated for
v1.0.0"}` (e.g. `transcript.md:810-813`) every single time. The real, and only, failure in every case
is `story_markdown`: `{"status": "error", "message": "Fails Definition of Ready/Done... user_story is
missing, still the template placeholder, or has every slot left blank."}` (`transcript.md:803-809`) -
a content-quality rejection from `_story_readiness_issues`, nothing to do with roadmap syncing at
all. `advance_story_stage` (`agents/scrum_team/tools/requirements.py:2023-2045`) computes
`synced = story_md_result.status == "ok" and roadmap_result.status == "ok"` and, whenever either half
fails, always emits the same generic, roadmap-first-worded message (`requirements.py:2035-2039`)
regardless of which one actually failed.

This is purely a diagnostic-wording gap, not a correctness bug: the story's stage *is* still
correctly recorded in state either way (`requirements.py:2011-2013`'s own comment: "the story's own
stage IS recorded in state regardless of the markdown/roadmap sync outcome"), and in every observed
occurrence the agent correctly self-corrected on its very next call by reading `story_markdown`'s
real sub-message and calling `upsert_story` with real content (`transcript.md:817-831` immediately
follows the rejection at `transcript.md:791-815`) - the team was never actually blocked by this. The
problem is purely that a human (or an agent) skimming the top-level `message` alone, as the maintainer
did, reasonably concludes "the roadmap sync is broken" when it is working correctly every time this
was observed.

## Acceptance Criteria
- `advance_story_stage`'s rejection message, when only one of `story_markdown`/`roadmap_sync` failed,
  must name the one that actually failed - not a generic "X and/or Y failed" that reads as a roadmap
  problem even when `roadmap_sync.status == "ok"`.
- When *both* sub-checks genuinely fail, the message may still reference both, but must not claim a
  sync failed when its own sub-result says `"status": "ok"`.
- A new regression test calls `advance_story_stage` in a state that fails only the story-markdown
  check (as in every observed `0.1.0-run73` occurrence) and asserts the returned `message` names
  `story_markdown`/the content issue, not "roadmap sync," when `roadmap_sync.status == "ok"`.

## Notes
- Lives entirely in `agents/scrum_team/tools/requirements.py:2032-2039` (`advance_story_stage`'s
  `synced`/`message` construction) - no other file needs to change.
- Not related to SPEC-ISSUE-0070 (filed from the same run) beyond both surfacing from the same
  maintainer review of `0.1.0-run73`'s logs - this is a message-wording fix with no bearing on the
  sprint-phase lock.

## Test Approach
- `agents/scrum_team/tests/test_requirements.py`: extend/add a test mocking `_update_story_markdown`
  to return `{"status": "error", "message": "..."}` and `_sync_roadmap_for_story` to return
  `{"status": "ok", ...}` - assert the rejection message does not read as a roadmap-sync failure
  (e.g. doesn't lead with "syncing specs/ROADMAP.md... failed") and does surface the real
  story-markdown issue text. Add the mirror case (roadmap fails, story markdown ok) to confirm that
  direction is also named correctly.

## Resolution
`advance_story_stage` (`agents/scrum_team/tools/requirements.py`) now computes `story_md_failed`/
`roadmap_failed` independently and builds one of four distinct messages: both ok (`None`, unchanged),
story-markdown-only failed (names the real DoR/content issue text, explicitly says "specs/ROADMAP.md
synced fine"), roadmap-sync-only failed (names the real roadmap error text, explicitly says "the
story's own markdown file wrote fine"), or both genuinely failed (names both). No case any longer
claims a sync failed when its own sub-result reports `"status": "ok"`.

Tests added in `agents/scrum_team/tests/test_requirements.py` (`TestAdvanceStoryStageGates`):
`test_sync_failure_message_blames_only_story_markdown_when_roadmap_sync_ok`,
`test_sync_failure_message_blames_only_roadmap_sync_when_story_markdown_ok`,
`test_sync_failure_message_names_both_when_both_genuinely_fail` - the first directly replays
`0.1.0-run73`'s observed shape (story-markdown DoR rejection, roadmap sync `"ok"`) and asserts the
message no longer reads as a roadmap failure.

Full suite (`agents/scrum_team/tests`, via `docker compose run`, properly project-scoped per
`lib_docker.compose_project_args("test")`) passes: 1099 of 1104 tests, the same 5 pre-existing
failures (`test_agent.py`'s identity-fallback test and 4 in `test_steering_proposals.py`) present and
unrelated - confirmed via `git stash` A/B comparison when investigating SPEC-ISSUE-0069.
