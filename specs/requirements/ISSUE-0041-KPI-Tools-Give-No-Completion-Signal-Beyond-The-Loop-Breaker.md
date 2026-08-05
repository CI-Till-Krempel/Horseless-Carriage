# Issue

- Issue ID: ISSUE-0041
- Title: KPI Tools Give No Completion Signal Beyond The Loop Breaker
- Status: Draft
- Priority: Could
- Owner: Architect
- Last Updated: 2026-08-05

## Overview
Found during the same investigation as the fix for `eval/adk/README.md` finding #12
(`_detect_repeated_call_loop`/`REPEATED_CALL_LOOP_THRESHOLD` in `agent.py`). QualityGuardian repeatedly
called `calculate_kpis()` and `update_sprint_report(...)` back to back many times in a single session -
including calls that **succeeded** - apparently unable to tell it had already made progress and should
move on (e.g. `transfer_to_agent` to hand off, or simply stop). The repeated-call loop breaker now
mechanically cuts this off after 3 identical calls in a row, which is a real, working fix for the
budget-burn this caused - but it's a backstop that only engages *after* several calls are already
wasted, and its error message ("stop repeating, do X instead") only fires on the call that trips the
threshold, not on the earlier successful calls that led up to it.

Both `calculate_kpis` and `update_sprint_report` (`agents/scrum_team/tools/quality.py`) currently
return a plain `{"status": "ok", ...}` on success, with no signal about what to do next. Adding an
explicit hint to that response - e.g. "KPIs recorded; transfer_to_agent to hand off next" once
`update_sprint_report` succeeds - is a smaller, cheaper defense-in-depth measure that could reduce how
often the mechanical breaker needs to fire at all, the same way `prompts.py`'s explicit
"NEVER call transfer_to_agent with agent_name=X" lines (ISSUE-0039's resolution) reduce, without
eliminating, self-transfers.

## Acceptance Criteria
- After a successful `update_sprint_report` call, its response includes explicit guidance that the KPI
  reporting step is complete and the model should transfer to another role (or stop), rather than a
  bare `{"status": "ok", "kpis": ...}`.
- A live eval run shows QualityGuardian's `calculate_kpis`/`update_sprint_report` call count per
  session measurably lower than before this change (qualitative check via `eval/adk/README.md`'s
  console-log conventions - see "Reading a live run's console output" - not a strict pass/fail gate).
- The repeated-call loop breaker (`_detect_repeated_call_loop`) remains in place regardless - this is
  additive defense-in-depth, not a replacement for the mechanical backstop.

## Notes
- This is explicitly the "nice to have" half of the fix for the same real problem
  `_detect_repeated_call_loop` (ISSUE tracked inline as `eval/adk/README.md` finding #12, not yet its
  own ISSUE file since it's already Done) already resolves mechanically - low priority precisely
  because the mechanical fix already works without it.
- Consider whether other tools with an implicit "do this once per sprint/session" shape (e.g.
  `create_sprint_report`) have the same gap, if this pattern turns out to help.

## Test Approach
- Unit test on `update_sprint_report`'s success response asserting the new hint text is present.
- Re-run `python3 run_adk_eval.py` before/after and compare QualityGuardian's per-session tool-call
  count from the console log/transcript.
