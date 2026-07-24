# Issue

- Issue ID: ISSUE-0007
- Title: Version and Priority Can Bypass plan_backlog_item and Desync the Roadmap
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-24

## Overview
`PO_PROMPT` (MANDATORY, lines 199-200): "Use `update_roadmap` to keep the release plan and roadmap
in sync with the backlog" and "Use `plan_backlog_item` to assign stories to versions and set
priorities." `plan_backlog_item` (`agents/scrum_team/tools/requirements.py`) does the right thing:
setting a `version` through it also calls `update_roadmap` in the same call, keeping
`specs/ROADMAP.md` in sync automatically. But `upsert_story`/`upsert_epic`/`upsert_issue` all funnel
into `upsert_backlog_item`, which happily accepts a `version` or `priority` key directly in the item
dict with no equivalent sync call and no guard rejecting it - unlike `status`, which
`blocks_direct_status_set` already closes off for exactly this class of bypass. A story upserted
with `{"version": "v0.2", ...}` directly gets silently recorded in `product_backlog` with that
version while `specs/ROADMAP.md` never learns about it, producing the same "roadmap doesn't reflect
reality" symptom this project has already fixed once for `status`.

## Acceptance Criteria
- `upsert_backlog_item` rejects (or mirrors `plan_backlog_item`'s behavior by also calling
  `update_roadmap`/`set_priority`) when `item.get("version")` or `item.get("priority")` is set
  directly, in the same style as the existing `blocks_direct_status_set` check just above it.
- Rejection message (if going the reject route, for consistency with the `status` guard) points the
  caller at `plan_backlog_item` instead.
- A test exists: `upsert_story({"version": "v0.2", ...})` either errors with a clear message or
  results in `specs/ROADMAP.md` actually reflecting the new version - either way, "silently
  desynced" is no longer possible.

## Notes
- Where the gap currently lives: `upsert_backlog_item` in `agents/scrum_team/tools/requirements.py`
  checks `blocks_direct_status_set(item.get("status"))` but has no equivalent check for `version`/
  `priority`.
- Same root cause class as the already-fixed direct-status-set bypass (see `blocks_direct_status_set`
  in `agents/scrum_team/helpers.py`) - this issue is that fix's sibling gap, not yet closed.
- Traces back to `agents/scrum_team/prompts.py` lines 199-200 (`PO_PROMPT`).

## Test Approach
- Unit test mirroring the existing `blocks_direct_status_set` tests: call `upsert_story`/
  `upsert_backlog_item` with `version`/`priority` set directly and assert the guarded behavior,
  then confirm `plan_backlog_item` still works as the sanctioned path.
