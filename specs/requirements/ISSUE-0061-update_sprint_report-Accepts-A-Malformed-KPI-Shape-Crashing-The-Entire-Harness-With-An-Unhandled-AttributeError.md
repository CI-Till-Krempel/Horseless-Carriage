# Issue

- Issue ID: ISSUE-0061
- Title: `update_sprint_report` Accepts A Malformed KPI Shape, Crashing The Entire Harness With An Unhandled `AttributeError`
- Status: Ready
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-14

## Overview
Reported (`0.1.0-run67`'s `report.md`): the run stopped early after only 2 of 5 requested sprints -
`Stop reason: crashed`, `Crashed during sprint: 3`, `Crash error: AttributeError: 'float' object has
no attribute 'get'`. Unlike every other eval-run issue filed this cycle (ISSUE-0053 through
ISSUE-0060), this isn't wasted budget or stalled progress inside a working guardrail - it's an
**unhandled Python exception that kills the entire process**, confirmed by the raw GH Actions log
(`gh run view 38574216935 --log`): `--- sprint 3/5 crashed: AttributeError: 'float' object has no
attribute 'get' ---`, immediately after ScrumMaster's closing sequence
(`calculate_kpis()` -> `update_sprint_report(kpis=...)` -> `create_sprint_report(...)`, raw log lines
2088-2092). `transcript.md` has no `## Raw agent activity log - sprint 3` section at all (only sprints
1-2 made it in) and `manifest.json`'s `sprints` array has only 2 entries - the harness's own
per-sprint append-to-manifest/transcript step only runs after a sprint completes normally, so the
crash lost sprint 3's structured record entirely, leaving only the raw log's truncated tool-call
previews as evidence.

**Confirmed mechanism.** `update_sprint_report(kpis: Dict[str, Any])` (`agents/scrum_team/tools/
quality.py:645-687`) accepts any dict-shaped `kpis` argument - it self-heals two specific malformed
shapes (the literal tool name as a string, or a JSON/Python-repr string), via `_coerce_dict_arg`, but
never validates that the dict actually matches the structure `calculate_kpis()` itself returns
(`team_effectiveness`/`result_quality`/`maintainability`/`security` each a nested dict, see
`quality.py:610-643`). Whatever shape survives `_coerce_dict_arg` is written straight to
`state["sprint_report_kpis"]` unchecked (`quality.py:685`). `ScrumState.sprint_report_kpis` itself is
declared as a bare `Dict` (`agents/scrum_team/state.py:41`) - no nested-value-type constraint - so a
malformed shape sails through that validation layer too.

`create_sprint_report` (`agents/scrum_team/tools/budget.py:1434-1470`) then renders the "KPI
Dashboard" section by reading that same unchecked dict:
```python
kpis = s.get("sprint_report_kpis") or {}
...
team_effectiveness = kpis.get("team_effectiveness") or {}
...
if team_effectiveness.get("say_do_ratio") is not None:
```
`kpis.get("team_effectiveness") or {}` only falls back to `{}` when the value is falsy (`None`,
missing, `0`, `0.0`) - a **truthy** non-dict value (e.g. `1.0`, `0.5`) passes straight through, and the
very next line's `.get()` call on it raises exactly `AttributeError: 'float' object has no attribute
'get'` - uncaught anywhere in the call chain, propagating all the way up through `run_eval.py`'s
sprint loop and killing the whole run.

The raw log's truncated `update_sprint_report(kpis=...)` previews across all 3 sprints are
circumstantial but consistent with the cheap eval model re-typing this dict from its own reading of
`calculate_kpis()`'s prior tool response rather than passing the literal object through unmodified -
Sprint 1's preview starts `{'security': {'vulne...`, Sprints 2 and 3's both start
`{'prompt_context_usa...` (raw log lines 1929, 2067, 2090) - three different first-key orderings for
what should be the same five-key dict in the same insertion order every time if passed through
verbatim. A "cheap" model collapsing one nested KPI sub-dict down to just its headline scalar (e.g.
writing `"team_effectiveness": 1.0` instead of `"team_effectiveness": {"say_do_ratio": 1.0, ...}`) when
re-serializing it from memory is exactly the shape of input this gap has no defense against - and
exactly matches the `'float' object` detail in the crash message (every KPI value `create_sprint_report`
pulls out *is* a plain float/int/None - `say_do_ratio`, `test_coverage`, `code_complexity`, etc. - see
`quality.py:595-621`).

This is a genuine guardrail gap, not just agent behavior under a working guardrail: `update_sprint_report`'s
own docstring already acknowledges a *related* risk ("a bad `sprint_report_kpis` shape there crashes
the *next* turn - regardless of which agent's - nowhere near this tool or this call") but that framing
undersold the actual severity - this run shows the crash happening immediately, synchronously, inside
`create_sprint_report` on the *same* turn, not deferred to some future state-revalidation. Nothing in
this codebase's otherwise-consistent "self-heal or reject cleanly" philosophy (the same function's own
two existing self-heal branches, or `resolve_story_blocker`/`advance_story_stage`'s many structured
`{"status": "error", "message": ...}` rejections) applies here - this is the one place a malformed tool
argument reaches all the way to an *uncaught* Python exception instead of a clean, retryable rejection.

## Acceptance Criteria
- `update_sprint_report` validates that `kpis` (after `_coerce_dict_arg`) has the expected nested shape
  - at minimum, that each of `team_effectiveness`/`result_quality`/`maintainability`/`security`, if
  present, is itself a dict (or absent/`None`) - rejecting with a clear, structured `{"status": "error",
  "message": ...}` (not a crash) if any of them is some other type, naming which key and what type it
  actually saw, so the agent can retry with calculate_kpis()'s real return value instead of its own
  reconstruction.
- `create_sprint_report`'s own KPI-rendering section (`budget.py:1434-1470`) is defensive even without
  relying solely on the gate above - e.g. explicitly checking `isinstance(..., dict)` before each
  `.get()` chain, not just `or {}` (which only catches falsy values, not truthy non-dicts) - so that
  even a `sprint_report_kpis` value written directly to state some other way (not necessarily via
  `update_sprint_report`) can never crash the report-rendering step.
- A new regression test: calling `update_sprint_report(kpis={"team_effectiveness": 1.0, ...})` (a
  truthy scalar standing in for a nested dict) is rejected, not silently accepted into state; a second
  test confirms `create_sprint_report` does not raise even if a malformed shape somehow still reaches
  `state["sprint_report_kpis"]` (defense in depth, per the bullet above).
- Does not require the agent to pass the *literal* object reference from `calculate_kpis()` (that's
  not enforceable over a tool-call boundary) - only that whatever shape it reconstructs is structurally
  valid.

## Notes
- Gap lives in `update_sprint_report` (`agents/scrum_team/tools/quality.py:645-687`, specifically the
  missing nested-shape check after `_coerce_dict_arg`) and in `create_sprint_report`'s own
  `kpis.get("X") or {}` pattern (`agents/scrum_team/tools/budget.py:1438-1441`) which doesn't defend
  against a truthy non-dict.
- `ScrumState.sprint_report_kpis: Dict` (`agents/scrum_team/state.py:41`) has no nested schema either -
  not necessarily something to change (a bare `Dict` is the right type at the pydantic-model level,
  the same way `blocked: Optional[Dict]` and others are elsewhere in this file), but worth noting this
  layer provides no safety net here, so the fix has to live in the tool layer above it.
- Distinct from every other issue filed this investigation cycle (ISSUE-0053 through ISSUE-0060): all
  of those degrade team performance (wasted budget, stalled progress, state regression) while the
  harness itself keeps running; this one terminates the run outright with an unhandled exception,
  losing that sprint's entire structured record (no `manifest.json`/`transcript.md` entry for sprint 3
  at all) - the highest-severity class of gap found so far.
- `report.md`'s own "Top Problems" for this run don't mention the crash mechanism at all (the judge-LLM
  only sees the post-crash repo/specs state, not the crash itself) - this is a case where Step 1-3's
  own artifact-pulling (the run's `conclusion`/`report.md` header, then the raw `gh run view --log`)
  was essential; `report.md` alone would have completely missed this.

## Test Approach
- `agents/scrum_team/tests/test_quality.py` (wherever `update_sprint_report` is tested): a case passing
  a `kpis` dict with one of the four nested keys set to a plain float/int instead of a dict - assert
  `status == "error"` and that `state["sprint_report_kpis"]` is unchanged (not silently written).
- `agents/scrum_team/tests/test_budget.py` (wherever `create_sprint_report` is tested): a case that
  directly seeds `state["sprint_report_kpis"] = {"team_effectiveness": 1.0}` (bypassing
  `update_sprint_report` entirely, simulating the defense-in-depth bullet) and confirms
  `create_sprint_report` still completes without raising, rendering KPI Dashboard with the malformed
  entry reported as unavailable/skipped rather than crashing.
- Re-run (or replay) `0.1.0-run67`'s Sprint 3 shape against the fixed harness and confirm a malformed
  `update_sprint_report` call is rejected and retried rather than crashing the run.
