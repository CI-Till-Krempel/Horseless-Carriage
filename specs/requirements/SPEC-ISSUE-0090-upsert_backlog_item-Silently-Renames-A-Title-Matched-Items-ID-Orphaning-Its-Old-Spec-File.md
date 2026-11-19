# Issue

- Issue ID: SPEC-ISSUE-0090
- Title: `upsert_backlog_item` Silently Renames A Title-Matched Item's ID, Orphaning Its Old Spec File
- Status: Ready
- Priority: Should
- Owner: Architect
- Last Updated: 2026-11-13

## Overview
A real eval run (0.2.1-run94) stalled hard: `report.md` scored Team Efficiency 1/5 ("severe process
thrashing ... Sprints 3, 4, and 5 accomplished zero user-facing feature work") and flagged Top
Problem #3 as "Redundant and duplicate security scan issue files cluttering the repository... File
tree contains identical files SPEC-ISSUE-0001, SPEC-ISSUE-0003, SPEC-ISSUE-0006, and SPEC-ISSUE-0007
all titled 'Release audit: security scan findings need triage'." The judge's suggested fix ("add
dedup checks before creating new issue files") is wrong about the mechanism - a dedup check already
exists and ran correctly every time. The bug is elsewhere.

**Confirmed mechanism.** `run_release_audit`'s `_file_release_audit_findings`
(`agents/scrum_team/tools/quality.py:556-560`) deliberately calls `upsert_issue` with **no `id`**,
relying entirely on title-match dedup ("re-running the audit against an already-tracked finding
updates that same Issue in place ... rather than filing a duplicate" - its own docstring,
`quality.py:531-534`). That title-match dedup (`upsert_backlog_item`, `requirements.py:987-988`) does
work. The break is in what runs *before* it: `upsert_backlog_item` (`requirements.py:958-960`)
unconditionally calls `_generate_next_id` and overwrites `item["id"]` whenever the caller's `id` is
missing or a placeholder - **before** `matches()` ever runs, with no awareness that a title match is
about to be found:

```python
if not item_id or item_id == placeholder:
    item_id = _generate_next_id(prefix, tool_context)
    item["id"] = item_id                      # requirements.py:960 - mutates item unconditionally

def matches(x):
    return (item_id and x.get("id") == item_id) or (title and x.get("title") == title)
```

When `matches()` then finds the old item by title (the second clause, since `item_id` here is the
*freshly generated* one, never the old one), the merge at `requirements.py:1014`,
`backlog[i] = {**x, **item}`, overlays the old item `x` (id `SPEC-ISSUE-0001`) with `item` (id
`SPEC-ISSUE-0003`, just generated) **in the same backlog slot** - silently renaming the live Issue's
ID in place, even though a real match was found and no new ID was ever needed.

That rename then reaches `_update_story_markdown` (`requirements.py:1437`), which derives the output
filename from `item["id"]` - now `SPEC-ISSUE-0003-...`. It writes that new file. Nothing deletes the
old `SPEC-ISSUE-0001-...md`. The transcript shows both coexisting on disk afterward -
`list_docs` in sprint 3 (`transcript.md:8008`) lists **both**
`specs/requirements/SPEC-ISSUE-0001-Release-audit:-security-scan-findings-need-triage.md` and
`specs/requirements/SPEC-ISSUE-0003-Release-audit:-security-scan-findings-need-triage.md` side by
side, byte-identical titles. `manifest.json`'s per-sprint `product_backlog` snapshots confirm the
pattern repeats every single release: the one live "security scan" Issue is `SPEC-ISSUE-0001` (sprint
1) -> renamed to `SPEC-ISSUE-0003` (sprint 2, at `run_release_audit`'s single call,
`transcript.md:7603-7650`, response `"issue_id": "SPEC-ISSUE-0003", "new": false`) -> `SPEC-ISSUE-0005`
(sprint 3) -> `SPEC-ISSUE-0006` (sprint 4) -> `SPEC-ISSUE-0007` (sprint 5) - one logical Issue, five
IDs, four of its five spec files abandoned on disk forever, none ever cleaned up.

This is a genuine code bug independent of the "sprints 3-5 shipped nothing" finding (Top Problem #2),
though it's a plausible contributor: an Issue whose ID keeps silently changing underneath the team is
harder for ProductOwner/ScrumMaster to track consistently sprint over sprint (`list_open_issues`,
`sprints_open`, `recurrence_count` all key off `id`, and every rename resets that bookkeeping to a
"new" item with `recurrence_count: 0`, `filed_sprint` reset to the sprint of the rename - see
`upsert_backlog_item`'s `filed_sprint`-stamping, `requirements.py:1026-1027`, which only special-cases
`not updated_item` (true new items), not this silent in-place rename).

## Acceptance Criteria
- `upsert_backlog_item` (or `_generate_next_id`) must not assign/overwrite `item["id"]` until *after*
  `matches()` has determined whether an existing item is being updated. If an existing item is found
  by title, the existing item's `id` is kept; a fresh ID is only ever generated for a genuinely new
  item (no match found).
- Calling `upsert_issue({"title": X, ...})` twice with the exact same title and no `id` both times
  must update the SAME backlog item/ID both times, and must not produce a second
  `specs/requirements/*.md` file for that title.
- A test exists: call `upsert_backlog_item({"type": "Issue", "title": T, "overview": O})` twice (no
  `id` on either call); assert both calls return the same `item["id"]`, the backlog has exactly one
  item with title `T`, and only one `specs/requirements/<id>-...md` file exists for it afterward.

## Notes
- Root cause: `agents/scrum_team/tools/requirements.py:958-960` (the premature `_generate_next_id`
  call/mutation) combined with `requirements.py:1014`'s `{**x, **item}` merge, which lets `item`'s
  (wrong) `id` field win over the matched existing item `x`'s (correct) one.
- `_file_release_audit_findings` (`agents/scrum_team/tools/quality.py:525-589`) is the caller that
  actually hits this every release, since it deliberately omits `id` by design - but any caller of
  `upsert_story`/`upsert_epic`/`upsert_issue` that omits `id` and happens to exactly match an
  existing title hits the same rename.
- Distinct from SPEC-ISSUE-0008 (Done): that issue covers *near-duplicate* titles from a caller
  providing a genuinely new item, and only adds a non-blocking warning - it doesn't touch the
  exact-title-match update path, which is the one this bug lives in.
- The old orphaned file is not merely clutter: it is stale content (the previous sprint's security
  scan counts) left permanently discoverable via `list_docs`/`read_doc` under a dead ID that
  `advance_story_stage`/`list_open_issues` no longer recognize, since the backlog item has moved on
  to the new ID.

## Test Approach
- Unit test in `agents/scrum_team/tools/requirements.py`'s test suite: two sequential
  `upsert_backlog_item` calls as described in Acceptance Criteria; assert ID stability and that no
  second spec file is written (`Path(specs/requirements).glob(f"*{title_slug}*")` has length 1).
- Regression test for `run_release_audit`: call it twice in sequence (simulating two releases) with
  the same advisory finding both times; assert `issues_filed[0]["issue_id"] ==
  issues_filed[1]["issue_id"]` on the second call.
