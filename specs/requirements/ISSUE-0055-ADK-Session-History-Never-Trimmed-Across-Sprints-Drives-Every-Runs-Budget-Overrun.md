# Issue

- Issue ID: ISSUE-0055
- Title: ADK's Native Session History Is Never Trimmed Across Sprint Boundaries - The Real Driver Behind Every Run's "Sprint Token Budget Exceeded" Finding
- Status: Ready
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-14

## Overview
Reported (`0.1.0-run64`'s `report.md`): the run stopped after 3 of 5 sprints with `stop_reason:
budget_critical_halt`, and - same as nearly every recent run (60, 62, 63, 64) - "Sprint token budgets
are consistently exceeded across all recorded sprints" is the #1 severity-high Top Problem, each time
with a vague "no clear fix - token consumption limits in multi-agent orchestration frameworks require
architectural pruning" suggested fix. ISSUE-0054 (filed from `0.1.0-run62`) found and fixed one real,
concrete contributor (`.hc/state.json` merge conflicts dumping the full scrum state into tool
responses) - but `0.1.0-run64` ran **after** that fix landed (`hc_commit: a4fc7c06...`, which already
has ISSUE-0054's fix) and still blew its budget on all 3 sprints it completed, with **zero**
`.hc/state.json` conflicts anywhere in its transcript. There is a second, larger, still-unaddressed
mechanism.

**Confirmed mechanism.** `run_eval.py`'s `_main_async` creates exactly **one** ADK session for the
entire 5-sprint run and reuses it for every sprint:

```python
session = await session_service.create_session(app_name=app_name, user_id=user_id)
...
for sprint_number in range(1, args.sprints + 1):
    ...
    sprint_result = await _run_one_sprint(
        runner, session_service, app_name, user_id, session.id, message_text, ...
    )
```

(`agents/scrum_team/scripts/run_eval.py:809-873` - no session is ever recreated inside that loop.)
`agent.py`'s own `history_management_after_callback` docstring (line ~1689) spells out exactly what
this means for every model call: *"google.adk.flows.llm_flows.contents._ContentLlmRequestProcessor
already replays the full conversation from invocation_context.session.events into
llm_request.contents by default... so on any normal, continuing session, llm_request.contents already
has the real history."* Nothing in this codebase ever trims, compacts, or resets ADK's own
`session.events` between sprints. `_trim_transcript`/`TRANSCRIPT_MAX_ENTRIES`
(`agents/scrum_team/agent.py:1607-1634`) sounds like it would help, but it only bounds a *separate*,
custom `state["transcript"]`/`state.messages` bookkeeping list, and that list is only ever replayed
into `llm_request.contents` in one narrow case - "a genuinely fresh ADK session (empty
session.events)" (the same function's own comment, line ~1709) - which never applies mid-run. The
ongoing, continuing-session case (every sprint after the very first) is untouched by any trimming
anywhere.

**Empirical confirmation, two runs, same monotonic signature** - average tokens consumed per
transcript event climbs every sprint, even as the number of *new* events per sprint *falls* (less new
work, not more, yet rising cost - the signature of cost scaling with cumulative session size, not
sprint-local activity):

| Run | Sprint 1 | Sprint 2 | Sprint 3 | Sprint 4 | Sprint 5 |
|---|---|---|---|---|---|
| `0.1.0-run62` (events / avg tokens-per-event) | 300 / 17,683 | 155 / 38,675 | 123 / 49,388 | 103 / 62,737 | 91 / 77,260 |
| `0.1.0-run64` (events / avg tokens-per-event) | 300 / 17,951 | 19 / 310,798 | 20 / 330,035 | - | - |

`0.1.0-run64`'s Sprint 2 needed only 19 events to close out cleanly (`Stop reason:
sprint_report_produced - 19 events`, `transcript.md:5406`) - a genuinely efficient sprint by event
count - yet cost 5,905,166 tokens, a **17x** jump in per-event average over Sprint 1's 17,951, with no
new large content (no state.json conflict, no giant write_file) anywhere in those 19 events. The
model's actual per-call *prompt* isn't small either - by the time Sprint 3 blew its budget, each of the
6 roles' `prompt_tokens` (reported by `calculate_kpis`'s `prompt_context_usage`, `transcript.md:5884-5927`)
would need to be enormous to account for this; that specific KPI field is a red herring, though -
`calculate_prompt_context_usage()` (`agents/scrum_team/tools/quality.py:88-130`) measures only each
role's **static, fixed system prompt** text, never the live, per-call conversation size, so it can't
and doesn't detect this. The real per-call cost is only visible via `update_token_usage_callback`
(`agents/scrum_team/agent.py:1379-1404`) reading `llm_response.usage_metadata.total_token_count` -
which faithfully reports whatever ADK actually sent as that call's prompt, i.e. the full replayed
session so far.

This is also why `0.1.0-run64` stopped at Sprint 3 specifically: Sprint 3's own budget was consumed by
a handful of calls before `create_release_pr`'s PR (#537) could be merged, leaving
`sprint_report_pending_release` stuck `True` even though `create_sprint_report` itself succeeded
(`manifest.json`: `sprint_report_pending_release: true`, `critical_halt: true` for sprint 3) -
`run_eval.py`'s `_sprint_should_abort_run` (line 395) correctly refuses to call that a clean close-out
and stops the run. The abort logic itself is not the bug; it's correctly reacting to a sprint that
never had enough of its own budget left, because most of it was already spent re-processing prior
sprints' history before any of Sprint 3's own work began.

## Acceptance Criteria
- A sprint's actual, model-facing conversation context (`llm_request.contents`, as ADK builds it from
  `session.events`) does not grow without bound across sprint boundaries - average tokens-per-event
  must not climb sprint-over-sprint the way it does today in both `0.1.0-run62` and `0.1.0-run64` when
  sprint-local activity (event count) is flat or falling.
- Whatever compaction/reset is introduced must preserve what a new sprint genuinely needs to act
  correctly: current backlog/sprint state (already reinjected fresh every turn via
  `sprint_status_injection_callback`/`phase_awareness_injection_callback`, independent of raw history),
  and enough recent conversational context to avoid repeating a question already answered this sprint -
  it does not need the *verbatim* tool-call/response soup of sprints several boundaries back.
- A regression test simulates several sprints' worth of `session.events` accumulation and asserts
  `llm_request.contents` size (or an equivalent proxy) is bounded after a sprint boundary, not
  monotonically growing with total run history.
- `_trim_transcript`'s existing `TRANSCRIPT_MAX_ENTRIES` bound either gets wired into whatever replaces
  the ongoing-session content (if the fix reuses that summarized path for every sprint boundary, not
  just session-empty recovery), or an equivalent, separately-tested bound is introduced for the
  ADK-native path - either way, no path through a multi-sprint run may rely on ADK's unbounded default
  replay once more than one sprint has run.

## Notes
- Gap lives in `run_eval.py`'s single long-lived session across `_main_async`'s sprint loop
  (`agents/scrum_team/scripts/run_eval.py:809-873`), combined with ADK's own default behavior (no
  compaction anywhere in `agents/scrum_team/agent.py` reaches the *ongoing*-session case - only the
  fresh-session recovery branch of `history_management_after_callback`, line ~1713, ever substitutes a
  bounded history for ADK's native one).
- Distinct from ISSUE-0054: that issue was real and is fixed, but only explains extra cost on sprints
  that actually hit a `.hc/state.json` conflict (`0.1.0-run62`'s Sprints 4-5). This issue explains the
  baseline climb present from Sprint 1->2 onward in *every* run checked, including `0.1.0-run64`, which
  ran after ISSUE-0054's fix and hit zero state.json conflicts.
- Likely the actual mechanism behind most of the "Sprint token budgets are consistently exceeded"
  Top Problem entries across runs 60/62/63/64's reports, previously treated as a vague, unfixable
  "architectural pruning" problem rather than a confirmed, localized gap (one unbroken session +
  ADK's own default full-replay + no compaction anywhere in the ongoing-session path).
- Not investigated further here: whether ADK itself exposes a supported summarization/compaction hook
  (vs. this harness needing to build its own, e.g. by periodically calling `session_service` to start a
  fresh session seeded with a `_trim_transcript`-style summary) - that's the next engineer's design
  call, informed by whatever `google-adk`'s current version actually supports.

## Test Approach
- A new test exercising `history_management_after_callback` (or wherever the fix lands) across a
  simulated sprint boundary: seed `session.events`/`llm_request.contents` with several sprints' worth of
  bulky synthetic tool-call entries, trigger a sprint boundary, and assert the next call's
  `llm_request.contents` size is bounded (not equal to the full accumulated history).
- `agents/scrum_team/tests/test_run_eval.py`: an integration-shaped test asserting token usage per
  sprint (mocked LLM responses with fixed `usage_metadata.total_token_count` per call) does not scale
  with which sprint number is running, for a fixed number of calls.
- Re-run (or replay) `0.1.0-run64`'s Sprint 2/3 shape against the fixed harness and confirm
  tokens-per-event stays roughly flat instead of climbing 17x.
