# User Story

- Story ID: US-0033
- Title: Context Window Optimization Metrics and Steering-Doc-Size Guardrail
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-11-12

## As a maintainer, I want visibility into how much of each role's context budget is consumed by its static system prompt, and a mechanical flag when a steering document grows past this project's own 250-line guideline, so that context-window pressure (GH-513) is measurable rather than something only discovered anecdotally when a model's quality degrades.

## Background

GH-513 ("Context Window optimizations") lists four ideas of very different size and risk:
1. Splitting user stories into tasks that fit a ~150k-token conversation budget.
2. **Starting a new conversation per story/subtask instead of per sprint** (today's behavior).
3. Compacting system prompts and filtering for relevant HC state.
4. Collecting metrics on how much of the context window is filled by system prompts, since
   context windows (and the role behind them) are configuration-specific.

Idea 2 is a major architectural change — it touches the whole sprint/session lifecycle, ADK
session management, the `run_eval` harness, and the story-pipeline state machine. It is
deliberately **NOT** attempted by this story: it's too large and risky to land safely as one
story-sized PR alongside the rest of the concurrent v0.3.0 work in flight on this codebase.
**It remains deferred, unplanned future work** — see Notes below.

Idea 1 (splitting user stories into context-sized tasks) is also out of scope here: it's a
planning/PO-workflow change, not a measurement or guardrail, and depends on idea 4 (real
numbers) existing first to even know what "fits" means in practice.

This story delivers idea 4 in full, and the concrete, testable half of idea 3 the issue itself
names explicitly ("steering documents should be less than 250 lines").

### What already existed before this story

`tools/quality.py::calculate_prompt_context_usage()` (added on a prior PR #306 review comment,
independent of this GH issue) already measured each role's *combined* static prompt
(`prompts.ROLE_PROMPT_TEXT`) in tokens against that role's configured model's context window,
and rendered it in the sprint report's "Per-Agent Prompt Context Usage" section (full detail
only). That covered "context windows are configuration/role specific" but gave only one number
per role — not which *individual* `prompt_modules/*.md` (or `spec-templates/DOD.md`/`DOR.md`)
file was actually responsible for it.

### Current prompt-module line counts (measured 2026-11-12, `wc -l agents/scrum_team/prompt_modules/*.md`)

```
   7  ScrumOrchestrator-identity.default.md
   9  QA-identity.default.md
   9  ScrumMaster-identity.default.md
  10  Architect-identity.default.md
  10  DevTeam-identity.default.md
  14  ProductOwner-identity.default.md
  51  ScrumOrchestrator-guardrails.md
  55  ProductOwner-guardrails.md
  56  Architect-guardrails.md
  58  ScrumMaster-guardrails.md
  61  QA-guardrails.md
  65  DevTeam-guardrails.md
  69  QA-workflow.md
  80  Architect-workflow.md
 179  DevTeam-workflow.md
 192  ProductOwner-workflow.md
 270  ScrumMaster-workflow.md       <- over the 250-line guideline
 361  ScrumOrchestrator-workflow.md <- over the 250-line guideline
```

`ScrumMaster-workflow.md` (270 lines) and `ScrumOrchestrator-workflow.md` (361 lines) already
exceed the issue's stated 250-line guideline. This story does **not** rewrite/trim either file —
safely shrinking someone else's accumulated workflow content without changing what's actually
mechanically enforced is its own careful, separate piece of work, and out of scope for a
measurement/guardrail story. It makes the fact mechanically visible instead (`doctor.py`), so it
stops being something only discovered by someone eyeballing `wc -l` by hand.

## Acceptance Criteria

- Given any role, when `tools/quality.py::calculate_prompt_module_breakdown()` is called, then it
  returns, per role, a list of `{file, lines, tokens}` entries — one per piece
  `prompts.py::prompt_module_pieces(role)` concatenates for that role (guardrails, workflow, then
  DOD/DOR if applicable) — token-counted via `litellm.token_counter`, reusing the same
  model-resolution convention `calculate_prompt_context_usage` already uses.
- Given `calculate_kpis()`, when it runs, then its return dict includes a `prompt_module_breakdown`
  key (alongside the existing `prompt_context_usage`), for every role in `ROLE_NAMES`.
- Given a sprint report rendered at full interaction-detail level, when KPIs include
  `prompt_module_breakdown`, then a new "Per-Agent Prompt Module Breakdown" section lists each
  role's files with their line/token counts; at lower detail levels the section is omitted (and
  named in the existing "omitted sections" notice), matching the gating already used for
  "Per-Agent Token Usage"/"Per-Agent Prompt Context Usage".
- Given `doctor.py` run against this project's own checkout, when any
  `agents/scrum_team/prompt_modules/*.md` file exceeds 250 lines, then a non-blocking WARNING
  names the file and its real line count (GH-513) — never an ERROR, since trimming a file safely
  is separate work this check only flags, not forces.
- Given a checkout with no `agents/scrum_team/prompt_modules/` directory at all (e.g. a bare
  `tmp_path` in a test, or a non-HC repo), when `doctor.py` runs, then the check is silently
  skipped — never an error.
- Given the "per-story/subtask conversation instead of per-sprint" idea from GH-513, it is
  explicitly **not implemented** by this story — see Notes.

## Notes

- **Deferred to future work (explicitly, not silently dropped):** "starting new conversations
  per story or subtask, not per sprint" — this needs its own dedicated design/story once it can
  be tackled in isolation; it touches `start_sprint`/`sprint_budget_reset_state_delta`
  (`tools/budget.py`), ADK session lifecycle, the story-pipeline state machine, and the
  `run_eval.py` harness. Also related/adjacent but separate: GH-515 ("system instructions
  changing mid-session breaks prompt-cache alignment") — investigate together if/when this is
  picked up, since per-subtask sessions would restart prompt-cache alignment far more often than
  today's per-sprint sessions do.
- Splitting user stories into context-sized tasks (the other GH-513 idea) is also left for a
  later, PO-workflow-focused story — it depends on real numbers from this one to be meaningful.
- "Compacting system prompts, filtering for relevant hc state" — no concretely safe, demonstrable
  win was found in this pass. `ScrumMaster-workflow.md`/`ScrumOrchestrator-workflow.md` are the
  two candidates the new line-count check flags (see table above); trimming either is deliberately
  left as separate, carefully-reviewed future work rather than done here as a drive-by edit on an
  unrelated story.
- `calculate_prompt_module_breakdown` deliberately duplicates `prompts.py`'s own file-reading
  (via the new `prompt_module_pieces(role)` helper) rather than refactoring `_load_role_prompt`
  to share code with it — keeps this purely-additive measurement helper from ever being able to
  change what a running agent's actual system prompt contains.

## Test Approach

- `agents/scrum_team/tests/test_prompts.py::TestPromptModulePieces` — `prompt_module_pieces`
  returns guardrails-then-workflow (then DOD/DOR where applicable), each piece's text genuinely
  appears in `_load_role_prompt`'s own output, and an unknown role raises.
- `agents/scrum_team/tests/test_quality.py::TestPromptModuleBreakdown` — every role present with
  guardrails/workflow entries, DOD/DOR roles include the checklist pieces (others don't), the
  per-piece token sum stays within 5% of `calculate_prompt_context_usage`'s own combined total,
  and `calculate_kpis()` includes the new key for every role.
- `agents/scrum_team/tests/test_budget.py::test_create_sprint_report_renders_per_agent_prompt_module_breakdown`
  — the new sprint-report section renders at full detail with real file/line/token values.
- `tests/test_doctor.py::TestPromptModuleLineCountCheck` — a file over 250 lines warns with its
  real line count and the GH-513 reference; a file at/under the limit doesn't; a missing
  `prompt_modules/` directory is silently skipped (never an error); multiple oversized files are
  all reported.
- Full suite green: `python3 run_tests.py` (host-side `tests/` via pytest, plus the Docker-based
  `agents/scrum_team/tests` suite).
