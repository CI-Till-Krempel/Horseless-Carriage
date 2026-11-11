# Issue

- Issue ID: SPEC-ISSUE-0085
- Title: `_update_story_markdown` Crashes The Entire Eval Run When `value_hypothesis`/`rationale`/`test_approach` Is A List Instead Of A String
- Status: Ready
- Priority: Must
- Owner: Architect
- Last Updated: 2026-11-11

## Overview
`0.2.0-run87` (`gh run 42991455949`, artifact `eval-report-0.2.0-run87`) stopped after only 1 of 5
requested sprints: `report.md` reports `stop_reason: crashed`, `crash_sprint: 2`,
`crash_error: TypeError: can only concatenate str (not "list") to str`. `run_eval.py`'s per-sprint
loop (`agents/scrum_team/scripts/run_eval.py:1123-1134`) only records `f"{type(e).__name__}: {e}"`
before giving up on the whole run - no traceback is captured anywhere (not in `manifest.json`, not in
the raw Action log), so the crash site had to be found by reasoning about the last tool call before
it and reproducing directly.

The raw step log (`gh run view 42991455949 --log`) shows the crash immediately follows:

```
[Sprint 2 | Phase: Development | Tokens: 476,285/5,000,000] 🔧 [DevTeam] plan_sprint_backlog_item(plan={'estimate': 100, 'r..., title_or_id="SPEC-ISSUE-0001")
--- sprint 2/5 crashed: TypeError: can only concatenate str (not "list") to str ---
```

`SPEC-ISSUE-0001` here is itself a prior eval-filed Issue ("Ensure all team roles leave engagement
comments on the sprint backlog PR prior to feature implementation") that Sprint 2 picked up as sprint
work - in the preceding exchange ProductOwner had just gone through several `upsert_issue`/
`advance_story_stage` round-trips to get it to `Ready` (it initially failed Definition-of-Ready with
no `acceptance_criteria`, then failed again trying to set `status` directly through `upsert_issue`,
before succeeding on a third attempt - see the log lines immediately before the crash). DevTeam then
called `plan_sprint_backlog_item(title_or_id="SPEC-ISSUE-0001", plan={...})`, which is the call that
crashed.

**Confirmed root cause, reproduced directly (not inferred from the error string alone).**
`plan_sprint_backlog_item` (`agents/scrum_team/tools/scrum.py:1169`) merges the caller's `plan` dict
into the backlog item and calls `_update_story_markdown(updated_item, tool_context)`
(`scrum.py:1237`). `_update_story_markdown` (`agents/scrum_team/tools/requirements.py:1340`) builds
the story's "Notes" section like this:

```python
# requirements.py:1419
notes = item.get("value_hypothesis", "") or item.get("rationale", "")
...
# requirements.py:1472
content = header + "## Notes\n" + notes + "\n\n" + next_section
```

Neither `value_hypothesis` nor `rationale` is type-checked or coerced to `str` before this
concatenation - unlike `acceptance_criteria` a few lines above, which already has exactly this
defensive handling (`requirements.py:1418`:
`ac_text = "\n".join([...]) if isinstance(ac, list) else str(ac)`). If a role passes a list for either
field - e.g. drafting "rationale" as several bullet points instead of one string, which is a very
natural thing for an LLM free-text field to produce - `notes` becomes a `list`, and line 1472's
`str + list` concatenation raises exactly the observed `TypeError`, uncaught, straight out of
`_update_story_markdown`, `plan_sprint_backlog_item`, and every other caller
(`upsert_issue`/`upsert_story`/`advance_story_stage` all funnel into the same function - see
`requirements.py:994,2329,2631,2772,2945`).

Reproduced directly with the real function, isolated from the real repo
(`STATE_REPO_PATH` pointed at a scratch dir with a copy of `spec-templates/`):

```python
item = {
    "title": "Ensure all team roles leave engagement comments on the sprint backlog PR prior to feature implementation.",
    "overview": "...", "owner": "ScrumMaster", "type": "Issue", "id": "SPEC-ISSUE-0001",
    "priority": "Must", "status": "Ready",
    "acceptance_criteria": ["Engagement comments verified", "Document process"],
    "approach": "Ensure team engagement checks and process are clear.",
    "tasks": ["Verify engagement comments are supported", "Document process"],
    "estimate": 100,
    "rationale": ["Keeps the process transparent", "Avoids silent skips"],  # list, not str
}
_update_story_markdown(item, ctx)
```
```
Traceback (most recent call last):
  File "agents/scrum_team/tools/requirements.py", line 1472, in _update_story_markdown
    content = header + "## Notes\n" + notes + "\n\n" + next_section
              ~~~~~~~~~~~~~~~~~~~~~~^~~~~~~
TypeError: can only concatenate str (not "list") to str
```

A matching, undefended `test_approach` field (`requirements.py:1442-1444`) has the mirror-image bug:
`test_approach = item.get("test_approach", "")` followed by `test_approach +=
"\n\n### Tasks\n" + "\n".join(...)` crashes the same way (`list += str`, a different but equally
uncaught `TypeError`) if `test_approach` itself is ever a list rather than a string.

This is squarely a code-level gap, not "the agent had a bad run": every other content-validity
problem in this exact function (missing title, placeholder user story, empty acceptance criteria -
`_story_readiness_issues`, `requirements.py:431-454`) is caught and turned into a graceful
`{"status": "error", "message": ...}` the calling agent can read and correct, exactly like it did
three times in a row for the `Ready`/`acceptance_criteria` gates right before this crash. Only
`value_hypothesis`/`rationale`/`test_approach` skip that treatment and take the entire run down
instead - of 2,923,004 tokens spent and only 1 of 6 user stories shipped (`report.md`'s Top Problems
#1), this crash alone is why the run stopped at sprint 2 rather than continuing with the other 4.

## Acceptance Criteria
- `_update_story_markdown` coerces `value_hypothesis`/`rationale` (feeding `notes`) and
  `test_approach` to `str` the same way `acceptance_criteria` already is (`isinstance(x, list)` ->
  join/format, else `str(x)`) - a list value for any of these three fields must never raise, and must
  render as readable text in the generated story/issue markdown instead.
- A regression test calls `_update_story_markdown` (or `plan_sprint_backlog_item`/`upsert_issue`)
  with `rationale` as a list of strings and asserts it returns `{"status": "ok", ...}` with a written
  file, not an uncaught exception. Add the mirror case for `test_approach` as a list.
- No change to the existing `acceptance_criteria` coercion behavior or to any other field's handling.

## Notes
- Lives entirely in `agents/scrum_team/tools/requirements.py:1419-1444,1472` (`_update_story_markdown`'s
  `notes`/`test_approach` construction) - every caller (`plan_sprint_backlog_item`, `upsert_issue`,
  `upsert_story`, `advance_story_stage`) goes through this one function, so the fix is in exactly one
  place.
- `run_eval.py`'s crash handling (`run_eval.py:1123-1134`) only persists `str(e)`, no traceback - worth
  a separate, lower-priority follow-up (capture `traceback.format_exc()` into the manifest) so a future
  crash like this doesn't again require reproducing the bug from a truncated log line to find the exact
  site, but that's a diagnostics improvement, not part of this fix.

## Test Approach
- `agents/scrum_team/tests/test_requirements.py`: new test(s) mocking/calling `_update_story_markdown`
  directly with `rationale` (or `value_hypothesis`) set to a list of strings, asserting
  `status == "ok"` and that the written markdown's "## Notes" section contains the list's content as
  readable lines rather than raising. A second test does the same for `test_approach` as a list,
  combined with a non-empty `tasks` list (the exact combination that hits the second concatenation).
- Confirm via `plan_sprint_backlog_item`/`upsert_issue` integration-level tests (not just the private
  helper) that a list-valued `rationale`/`test_approach` passed through the public tool surface no
  longer raises.
