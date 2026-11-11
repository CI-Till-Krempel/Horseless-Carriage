# Issue

- Issue ID: SPEC-ISSUE-0087
- Title: `_story_readiness_issues`'s `(item.get("user_story") or "").strip()` Crashes The Entire Run With An Unhandled `AttributeError` When `user_story` Is A Dict
- Status: Ready
- Priority: Must
- Owner: Architect
- Last Updated: 2026-11-11

## Overview
Reported by `0.2.0-run89`'s `report.md`: the run stopped after **0 of 5** requested sprints -
`Stop reason: crashed`, `Crashed during sprint: 1`, `Crash error: AttributeError: 'dict' object has
no attribute 'strip'`. `manifest.json`'s `sprints` array is empty and `transcript.md` has no agent
activity at all (the harness's per-sprint manifest/transcript append only runs after a sprint
completes normally - a crash this early loses the entire sprint's structured record, same loss
pattern as SPEC-ISSUE-0061). The only surviving evidence is the raw CI step log
(`gh run view 43041747074 --log`, confirmed via `gh run list --workflow "Team Performance
Evaluation"`), which shows no Python traceback at all - only the one-line summary
`--- sprint 1/5 crashed: AttributeError: 'dict' object has no attribute 'strip' ---` - so the exact
call chain below had to be reconstructed from the preceding tool-call sequence plus the actual source.

**Confirmed mechanism.** The raw log's immediately-preceding sequence (`run89.log:1737-1750`) is:
1. `ProductOwner` creates US-0001, advances it to `Draft`, then to `Ready`.
2. `advance_story_stage(stage="Ready", ...)` fails cleanly: `'US-0001' is recorded as Ready in state,
   but writing the story's own markdown file failed - Fails De[finition of Ready]...`
3. `ProductOwner` retries with `upsert_story(story={'acceptance_criteri...)` - fails cleanly again:
   `Fails Definition of Ready/Done (see spec-templates/DOR.md, DOD.md) for status 'Ready' - not
   written:...`
4. `ProductOwner` retries a third time, described in its own preceding text as "Updating US-0001 with
   **structured** `user_story` field for DoR compliance" - `upsert_story(story={'id': 'US-0001',
   't'...)` (truncated in the log's own tool-call preview, no untruncated copy recoverable from this
   log). 17ms later: the crash.

The cheap eval model, after two plain-string rejections, reinterpreted `spec-templates/DOR.md`'s
requirement ("Story has a clear 'As a ... I want ... so that ...' statement", `DOR.md:18`) as calling
for a **structured object** (e.g. `{"as_a": ..., "i_want": ..., "so_that": ...}`) instead of a plain
string - nothing in `DOR.md` or any tool docstring suggests this shape, but nothing rejects it either.

That third call reaches `upsert_story` (`agents/scrum_team/tools/requirements.py:520-529`), which
only coerces the **top-level** `story` argument via `_coerce_backlog_item_dict`/`_coerce_dict_arg`
(`requirements.py:513-517`, `tools/base.py:623-649`) - recovering a JSON/repr string back into a
dict, but never inspecting the *types of individual fields inside* it. `ScrumState.product_backlog`
is a bare `List[Dict]` (`agents/scrum_team/state.py`) with no nested schema either, so a dict-shaped
`user_story` sails through both layers unchanged, exactly the same class of gap as SPEC-ISSUE-0061's
`sprint_report_kpis: Dict`.

`upsert_backlog_item` (`requirements.py:878`) stores the item and calls `_update_story_markdown`
(`requirements.py:990-991`), which - because `status` is `"Ready"` (`requirements.py:1394`) - calls
`_story_readiness_issues(item, is_epic, is_issue)` (`requirements.py:1395`). That function
(`requirements.py:431-454`) does:
```python
user_story = (item.get("user_story") or "").strip()   # requirements.py:449
```
`(x or "")` only substitutes `""` when `x` is falsy. A non-empty dict is truthy, so `or ""` does
nothing and `.strip()` is called directly on the dict - `AttributeError: 'dict' object has no
attribute 'strip'`, uncaught anywhere in the call chain, propagating up through `run_eval.py`'s
sprint loop and killing the entire run (same failure shape as SPEC-ISSUE-0061: a malformed tool
argument reaching an *uncaught* exception instead of this codebase's otherwise-consistent "self-heal
or reject cleanly" philosophy - the same function's own two earlier calls in this exact sequence
*did* reject cleanly with a structured `{"status": "error", ...}` message).

The identical `(item.get(...) or "").strip()` pattern, equally unguarded, also exists two lines above
for `title` (`requirements.py:441`) and for `overview`/`description` (`requirements.py:445`) - any of
the three fields receiving a dict/list/other non-string truthy value crashes the same way. A second,
independent unguarded use of `user_story` exists further down in the same function
(`requirements.py:1411-1415`, `1459`: `content.replace(..., user_story)`, which would raise
`TypeError: replace() argument 2 must be str, not dict`) - moot in this run only because the crash at
line 449 happens first, but confirms `user_story`'s assumed-string type is never checked anywhere in
this file.

## Acceptance Criteria
- `_story_readiness_issues` (`requirements.py:431-454`) validates that `title`, `user_story`, and
  `overview`/`description` are each a `str` (or absent/`None`) before calling `.strip()` on them - a
  non-string truthy value (dict, list, int, etc.) must be rejected with a clear, structured
  `{"status": "error", "message": ...}` DoR-failure (naming the field and the type actually seen),
  never an uncaught exception.
- `_update_story_markdown`'s own later `user_story` use (`requirements.py:1411-1415`, `1459`) is
  defensive independently of the gate above, so a non-string value that somehow reaches this far
  still can't crash the markdown-rendering step.
- A new regression test calls `upsert_story`/`_story_readiness_issues` with `user_story` (and
  separately `title`, and `overview`) set to a dict in a story whose status is `"Ready"` and asserts a
  clean `status == "error"` rejection naming the field and type, not a raised `AttributeError`.
- Replaying `0.2.0-run89`'s Sprint 1 shape (a dict-shaped `user_story` on a `Ready` story) against the
  fixed harness must be rejected and retried, not crash the run.

## Notes
- Gap lives entirely in `agents/scrum_team/tools/requirements.py`: `_story_readiness_issues`
  (`:431-454`, specifically the three `(item.get(...) or "").strip()` calls at `:441`, `:445`, `:449`)
  and the later unguarded `user_story` use in `_update_story_markdown` (`:1411-1415`, `:1459`).
- No layer above this ever validates per-field types: `upsert_story`/`_coerce_backlog_item_dict`
  (`:520-529`, `:513-517`) only coerces the top-level argument into *a* dict, and
  `ScrumState.product_backlog: List[Dict]` has no nested schema - exactly the same shape of gap as
  SPEC-ISSUE-0061 (`update_sprint_report`'s unchecked `kpis` nested dict), just on the story-content
  path instead of the KPI path.
- No full Python traceback was recoverable from this run - the harness only logs the exception's
  `str()` summary (`run89.log:1750`), not `traceback.format_exc()`; the call chain above was
  reconstructed from the immediately-preceding tool-call sequence plus reading the actual source, per
  this investigation's Step 3. Worth noting for whoever eventually looks at the harness's crash
  logging generally, though out of scope for this fix.
- `report.md`'s own "Top Problems" (judge-LLM, post-crash repo state only) correctly flagged "zero
  application code" and an incomplete backlog but never mentions the crash mechanism itself - same gap
  noted in SPEC-ISSUE-0061: the raw `gh run view --log` was essential, `report.md` alone would have
  missed this entirely.

## Test Approach
- `agents/scrum_team/tests/test_requirements.py`: extend/add a case calling `_story_readiness_issues`
  (or `upsert_story` end-to-end) with `user_story={"as_a": "...", "i_want": "...", "so_that": "..."}`
  on an item with `status="Ready"` - assert a structured `status == "error"` response naming
  `user_story` and its actual type, not a raised exception. Mirror cases for `title` and
  `overview`/`description` set to a dict.
- A case seeding `_update_story_markdown`'s `item["user_story"]` directly with a non-string value
  (bypassing the gate above, simulating defense-in-depth) and confirming the function still returns a
  structured error rather than raising inside the `content.replace(...)` call at `:1459`.
