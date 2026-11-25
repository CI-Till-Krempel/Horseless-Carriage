# User Story

- Story ID: US-0034
- Title: Runtime Context Utilisation KPI
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-11-25

## As a maintainer, I want every LLM call's context-window fill (prompt tokens vs. the role's model's context window) tracked and reported per role and per sprint as a KPI, so that I can tell when a role is running close to its model's limit and trigger a model-choice change or system-prompt/history optimization before quality degrades.

## Background

GH issue #566: "The context utilisation (I mean how much of the context is filled) should be
tracked and reported as a KPI, so the model choice and system prompt optimization can be
triggered if necessary."

US-0033 (GH-513) already measures the **static** part: `tools/quality.py::calculate_prompt_context_usage()`
token-counts each role's concatenated system prompt (`prompts.ROLE_PROMPT_TEXT`) against its
model's context window, once, in `calculate_kpis()`. That number is constant for a given checkout
and says nothing about how full the context actually gets at runtime, once conversation history,
the injected sprint-status/phase blocks and tool results are added on top of the prompt - which is
what actually fills a window (US-0033's own deferred "new conversation per story/subtask" idea
exists precisely because per-sprint sessions grow).

Before this story, the only per-call signal recorded was `update_token_usage_callback`
(`agents/scrum_team/agent.py`) adding `usage_metadata.total_token_count` into
`state.token_usage` for budget accounting. `prompt_token_count` - the provider-reported input size
of that exact call - was read nowhere.

## Acceptance Criteria

- Given any LLM call with `usage_metadata.prompt_token_count > 0`, when the after-model callback
  chain runs, then `track_context_utilisation_callback` folds `prompt_tokens / context_window`
  into `state["context_utilisation"]["sprints"][<sprint_number>]["agents"][<role>]`
  (`calls`, `sum_percent`, `sum_prompt_tokens`, `peak_percent`, `peak_prompt_tokens`,
  `high_water_calls`, plus the `model`, `context_window_tokens` and `window_source` it used).
- Given the role's model alias, when the context window is resolved
  (`tools/quality.py::resolve_context_window`), then the order is: `CONTEXT_WINDOW_OVERRIDES`
  (`<model>=<tokens>,...`) → the LiteLLM proxy's `/model/info` `max_input_tokens` (cached per
  process for 10 minutes, failures cached too) → `litellm.get_model_info(model)` →
  `CONTEXT_WINDOW_FALLBACK_TOKENS` (default 128000), and the source used is recorded; a
  fallback-sourced window is called out as an estimate wherever it's reported.
- Given a call whose prompt fills at least `CONTEXT_UTILISATION_HIGH_WATER_PERCENT` (default 80)
  of the window, then it counts as a high-water call, and a role whose peak reaches it is listed in
  `flagged_roles`.
- Given the callback runs, then it never writes `token_usage`/`budgets` or any other budget state,
  never returns a response (so it can't short-circuit the callback chain), and swallows any error -
  budget enforcement behaviour is unchanged (AGENTS.md section 12).
- Given no usage metadata, a non-numeric prompt token count, or no calls recorded for a sprint,
  then nothing is recorded / the summary reports `available: False` with a note - never a
  fabricated 0%.
- Given `calculate_kpis()`, then its result includes `context_utilisation` (the current sprint's
  summary: team mean/peak/high-water calls, per-role breakdown, flagged and estimated-window roles).
- Given a sprint report (`create_sprint_report`), then a "Context Utilisation" section shows the
  team headline and any context-pressure advisory at every interaction level, and the per-agent
  breakdown at "full" detail only (named in the omitted-sections notice otherwise). The
  mechanically-rendered fallback report includes the same section.
- Given an eval run, then each sprint's manifest entry carries the `context_utilisation` summary
  and the eval report's KPI Trends add "Context Utilisation (mean %)", "(peak %)" and
  "(calls >= high-water)" series.
- Given the dashboard, then the budget cell shows the current sprint's
  `ctx mean X% / peak Y% (N ≥80%)` when data exists, and nothing otherwise.
- Given an existing state repo without `context_utilisation`, then it still loads unchanged
  (additive, optional `ScrumState` field with an empty-dict default - no migration needed) and the
  key is persisted to `.hc/state.json` from then on (`REPO_STATE_KEYS`).

## Notes

- GH issue #566. Builds on US-0033 (GH-513), which covers the static system-prompt share of the
  same window.
- Keyed by sprint number rather than reset by `start_sprint`/`sprint_budget_reset_state_delta`, so
  the budget reset path (a guarded budget mechanism) is untouched and a few sprints of history stay
  available; pruned to the most recent 10 sprints to bound state size.
- Pure aggregation/rendering lives in `agents/scrum_team/context_utilisation.py` (stdlib only), so
  the host-side `dashboard.py` and the eval analysis script can import it without ADK/litellm.
- `prompt_token_count` is the provider-reported input size, so it already includes the system
  prompt, history, injected status blocks and tool results - exactly "how full the context is" for
  that call. Prompt-cached tokens are still part of the context and are counted.
- Not done here (possible follow-ups once real numbers exist): automatically switching models or
  compacting history when a role crosses the threshold; this story only measures and reports.

## Test Approach

- `agents/scrum_team/tests/test_context_utilisation.py` - aggregation (mean/peak/high-water per
  role and team, per-sprint separation, pruning, non-mutation, no-op inputs, unavailable summary),
  env parsing (threshold, overrides, fallback), report-section rendering per detail level,
  `resolve_context_window` resolution order and caching, the callback (records, never touches
  `token_usage`/`budgets`, tolerates missing/non-numeric metadata and errors, registered after
  `update_token_usage_callback` on every agent), `ScrumState` default/`REPO_STATE_KEYS`,
  `calculate_kpis()` key, and both sprint-report renderers.
- `agents/scrum_team/tests/test_run_eval_analysis.py::TestKpiTimeSeries::test_context_utilisation_series_from_sprint_summary`.
- `tests/test_dashboard.py::TestProjectSnapshot::test_context_utilisation_*`.
- Full suite: `python3 run_tests.py`.
