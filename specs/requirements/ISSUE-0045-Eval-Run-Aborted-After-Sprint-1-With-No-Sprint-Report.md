# Issue

- Issue ID: ISSUE-0045
- Title: Eval Run Aborted After Sprint 1 With No Sprint Report - Requirements Engineering Also Front-Loaded Outside The Sprint
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-08-07

## Overview
Reported (maintainer, reviewing `0.1.0-run25`): a 5-sprint evaluation run produced only 1 sprint and no
sprint report at all, prompting two questions - should `EVAL_SPRINT_TOKEN_BUDGET` simply go up again (it
was already raised once, by ISSUE-0035/GH issue #81), and is requirements engineering supposed to happen
outside the sprint, since that's what the transcript looked like.

Investigated directly against the real run: `gh run view 31156101729` (`0.1.0-run25`), its
`report.md`/`manifest.json`/`transcript.md` artifacts, and the actual PRs/branches merged into the eval
repo (`horseless-carriage-eval-todo-app`). Three distinct, compounding root causes were found - raising
the token budget alone would not have fixed two of them.

**Root cause 1 (requirements engineering IS front-loaded outside the sprint, confirmed):**
`create_sprint_backlog_pr` mechanically refuses to run until the Ready backlog holds
`READY_BACKLOG_SPRINTS_TARGET` (default 2) x `TARGET_STORIES_PER_SPRINT` (default 3) = 6 Ready stories
(`helpers.py`'s `ready_backlog_sprints_target`/`target_stories_per_sprint`, gated in
`tools/github.py::create_sprint_backlog_pr`). The fixed eval scenario's entire product backlog is exactly
6 stories, so this gate demanded the *whole* backlog be drafted, spec'd, and merged before Sprint 1 could
even publish its backlog. Confirmed against the eval repo's actual PR timestamps: all six
`story-spec/US-000{1-6}` PRs merged between 07:05:16 and 07:06:04, while `create_sprint_backlog_pr`
(PR #138) didn't succeed until 07:06:13 - i.e. ~all of the pre-implementation time in "Sprint 1" was
actually whole-product requirements engineering, not sprint-scoped work. This matches how the team's own
documented workflow (`92965ce`, "Add requirements-engineering sub-flow ... to diagram") draws it: a
"Requirements engineering & product workflow" subgraph that precedes Draft, outside the per-story sprint
pipeline. Per-agent token accounting for sprint 1 confirms the cost: ProductOwner alone used 1,525,209 of
the sprint's 4,072,826 tokens (37%) - largely this upfront backlog work - before any story reached
Implemented.

**Root cause 2 (the sprint closeout grace mechanism has a dead end):** `check_cost_budget_callback`
(`agent.py`) only grants extra budget headroom to finish the SPRINT CLOSE SEQUENCE
(retro -> `create_sprint_report` -> KPIs -> `create_release_pr`) to `SPRINT_CLOSEOUT_GRACE_ROLES`
(ScrumMaster/ProductOwner/QualityGuardian/ScrumOrchestrator - added by ISSUE addressed in `9e2cc41`).
DevTeam/QA/Architect get none of it, by design. But in `0.1.0-run25`, ProductOwner (a grace role)
transferred to DevTeam right as cumulative sprint token usage crossed the main ceiling; DevTeam - not a
grace role - was immediately hard-halted, and because `check_cost_budget_callback`'s halt response was
plain text with no tool call, DevTeam could not call `transfer_to_agent` to hand control to anyone who
*did* have grace. Every one of the harness's 4 remaining "continue" nudges re-invoked the same frozen
DevTeam and got the identical canned halt message (confirmed verbatim 5x in the transcript). The grace
allowance (127,174 tokens of headroom in this run) was never touched - the sprint ended with
`stop_reason: max_nudges_exhausted`, no sprint report, no release PR.

**Root cause 3 (the harness abandons all 5 sprints on any critical halt, even a clean one):**
`run_eval.py`'s `_main_async` treated `sprint_result["critical_halt"]` as an unconditional "stop the
whole run" signal, regardless of whether the sprint actually closed out (this was itself a deliberate,
documented choice from `9e2cc41`, made after an earlier run silently continued into a broken state and
crashed via an unrelated transfer-loop in the next sprint). Combined with root cause 2, this meant a
single sprint's budget pressure - even one that, with root cause 2 fixed, recovers cleanly via grace -
would still throw away sprints 2-5 entirely.

## Acceptance Criteria
- A non-grace role (DevTeam/QA/Architect) hard-halted by `check_cost_budget_callback` no longer freezes
  the sprint: its halt response carries a real `transfer_to_agent` hand-off to ProductOwner (a grace
  role) alongside the halt text, so ADK actually dispatches the transfer and ProductOwner gets a turn to
  attempt the close-out sequence with the remaining grace allowance - within the same turn, no extra
  "continue" nudge required. A grace role that has itself exhausted its own grace ceiling still just gets
  the plain halt text (nowhere better to redirect to).
- `run_eval.py` only aborts the remaining sprints when a critical halt left the sprint in a genuinely
  unclean state (no sprint report produced); a critical halt that still closed out cleanly (a real sprint
  report came out the other end) no longer throws away the rest of the run.
- The eval workflow (`.github/workflows/eval.yml`) sets `READY_BACKLOG_SPRINTS_TARGET=1` for its own
  isolated container only - not the real-usage default in `docker-compose.yaml`/`.local.yaml`/
  `.local-hostollama.yaml`, which stays at 2 for an open-ended real backlog - so Sprint 1 of this fixed
  6-story scenario no longer requires the entire backlog to be Ready before it can start.
- `EVAL_SPRINT_TOKEN_BUDGET`'s default is raised from 4,000,000 to 5,000,000 (`.env.example`,
  `.env.local.example`, `.github/workflows/eval.yml`), giving headroom above `0.1.0-run25`'s observed
  4,072,826 sprint-1 peak - now that roots 1-3 are fixed, this is real insurance rather than the only
  lever, since sprint 1's unavoidable one-time requirements-engineering cost is much smaller and sprints
  2-5 don't repeat it at all.
- Docs (`RELEASE.md`) reflect the new default and explain the `READY_BACKLOG_SPRINTS_TARGET` override and
  why it's eval-only.

## Notes
- Deliberately does NOT change the real-usage (non-eval) default for `READY_BACKLOG_SPRINTS_TARGET` - a
  2-sprint Ready buffer is a reasonable guard against thin sprints for an open-ended real backlog; the
  problem is specific to a fixed, small (6-story) backlog exactly matching the buffer's total size, which
  only the eval scenario has.
- Deliberately targets the redirect at ProductOwner specifically, not any grace role - `prompts.py`'s
  existing "If you see a TOKEN BUDGET EXCEEDED..." guidance already frames ProductOwner as the one who
  drives retro (via transferring to ScrumMaster) -> `create_sprint_report` -> KPIs -> `create_release_pr`,
  so this matches behavior the team was already instructed to attempt, it just could never get a turn to
  try.
- The synthesized `transfer_to_agent` function_call uses the same mechanism
  `recover_fake_tool_call_callback` (`agent.py`) already relies on - mutating an `LlmResponse`'s content
  parts to include a real `function_call`, which ADK's `_postprocess_async` dispatches identically to a
  genuine model-issued tool call (verified against `google/adk/flows/llm_flows/base_llm_flow.py`:
  a `before_model_callback`'s short-circuit response flows through the same post-processing and function
  dispatch as a real model response, including resuming the transferred-to agent within the same turn).
- `_sprint_should_abort_run`'s "was the close-out clean" check is `stop_reason == "sprint_report_produced"`
  specifically, not just "a sprint report exists" - the stronger, more precise signal that `_run_one_sprint`
  exited *because* a report appeared, not for some other reason while a stale report from state happened
  to be present.

## Test Approach
- `agents/scrum_team/tests/test_agent.py::TestSprintCloseoutGrace::test_non_grace_role_halt_redirects_to_product_owner_instead_of_freezing` -
  DevTeam/QA/Architect's halt response contains exactly one `transfer_to_agent(agent_name="ProductOwner")`
  function_call.
- `test_grace_role_also_hard_halts_once_the_grace_ceiling_is_exceeded` (extended) - a grace role past its
  own grace ceiling still gets a plain-text-only halt, no synthetic transfer.
- `agents/scrum_team/tests/test_run_eval.py` - `_sprint_should_abort_run` unit tests: no-halt cases never
  abort; a halt with `stop_reason="sprint_report_produced"` does not abort; a halt with any other
  stop_reason (`max_nudges_exhausted`, `max_events_reached`) does abort.
- Full `agents/scrum_team/tests` suite (via `docker compose --env-file .env.test run --rm --entrypoint ""
  -e PYTHONPATH=/app agent pytest --cov=agents agents/scrum_team/tests`, with `db`/`litellm` up first):
  507 passed, no regressions.
- `pytest tests/`: 421 passed, no regressions.

## Resolution
- `agents/scrum_team/agent.py`: new `_budget_halt_response()` - non-grace-role budget halts now carry a
  synthetic `transfer_to_agent(agent_name="ProductOwner")` function_call alongside the halt text; both the
  token-budget and USD-budget "exceeded" branches of `check_cost_budget_callback` now call it instead of
  building an inline text-only `LlmResponse`.
- `agents/scrum_team/scripts/run_eval.py`: new `_sprint_should_abort_run()` pure helper;
  `_main_async`'s per-sprint loop now only stops the whole run when a critical halt left no clean
  close-out, and logs (rather than aborting) when a halt recovered cleanly via grace.
- `.github/workflows/eval.yml`: `EVAL_SPRINT_TOKEN_BUDGET` default bumped 4,000,000 -> 5,000,000;
  `READY_BACKLOG_SPRINTS_TARGET=1` added for this workflow's isolated container only.
- `.env.example` / `.env.local.example`: `EVAL_SPRINT_TOKEN_BUDGET` default bumped to 5,000,000, comments
  updated.
- `RELEASE.md`: "Team performance evaluation" section updated with the new default and the
  `READY_BACKLOG_SPRINTS_TARGET` override/rationale.
