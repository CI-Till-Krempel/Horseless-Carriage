# Issue

- Issue ID: ISSUE-0034
- Title: Tool Calls Invisible In CLI Mode
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-07-29

## Overview
Follow-up question raised mid-session: "The tool call and human interaction requests have to be
visible in the user chat as well, is this implemented?"

**Investigation findings**:
- **Human interaction requests** (blocking interactions - GH issue #53/ISSUE-0025) were already
  covered: `record_blocking_interaction()` (`agents/scrum_team/tools/notifications.py`) always fires
  every configured notifier on every call, and the default `ConsoleNotifier` prints a hard-to-miss
  banner to stderr regardless of `AGENT_MODE`. Orchestrator-stall banners (ISSUE-0029) and budget-halt
  messages (`check_cost_budget_callback`) are additionally mechanically prepended to/returned as the
  visible model response text itself. No gap found here.
- **General tool calls** were a real, demonstrated gap for `AGENT_MODE=cli`: reading ADK's own CLI
  source (`google.adk.cli.cli.run_interactively`/`run_input_file`), the REPL only echoes an event if
  `''.join(part.text or '' for part in event.content.parts)` is non-empty - a pure
  `function_call`/`function_response` event has no `.text`, so it prints nothing at all. Every tool
  call a sub-agent made was completely invisible to anyone watching a foreground CLI session or
  `docker compose logs agent`, including gated actions worth noticing (e.g. `create_release_pr`
  refusing for lack of a fresh approval - which itself already goes through
  `record_blocking_interaction`, but a human would only see the *notifier's* banner, not the fact a
  tool call happened at all). `AGENT_MODE=web`'s `adk web` UI already renders a tool-call panel on its
  own, so this gap is specific to CLI/daemon-mode terminal visibility.

## Acceptance Criteria
- Every tool call made by any agent is visible somewhere a human watching a foreground session (CLI
  terminal, or `docker compose logs agent` for daemon mode) would see it - independent of whether the
  ADK frontend in use happens to render it itself.
- No tool argument *values* are logged (only argument names) - avoids dumping potentially large file
  contents/PR bodies, or leaking sensitive content, into logs.
- Purely a passive trace: never blocks, alters, or delays a tool call.
- Applies uniformly to every agent (all specialists + the Orchestrator), not just a subset.

## Notes
- This does not change anything about the already-implemented blocking-interaction/notification
  system (GH issue #53/ISSUE-0025, `docs/NOTIFICATIONS.md`) - that mechanism already worked correctly
  for "moments that need a human." This closes a separate, narrower gap: ordinary tool-call visibility
  in CLI mode specifically.
- Deliberately implemented as a `before_tool_callback` inside this repo rather than patching ADK's own
  CLI (a pip dependency, not part of this repo) - keeps the fix self-contained and frontend-agnostic.

## Test Approach
- `agents/scrum_team/tests/test_agent.py::TestLogToolInvocationCallback` - prints agent name + tool
  name + argument names to stderr; never leaks argument values; registered as `before_tool_callback`
  on every specialist agent and the Orchestrator.
- Full `agents/scrum_team/tests` suite (via `docker compose --env-file .env.test run --rm
  --entrypoint "" -e PYTHONPATH=/app agent pytest --cov=agents agents/scrum_team/tests`): 236 passed,
  no regressions.

## Resolution
- `agents/scrum_team/agent.py`: new `log_tool_invocation_callback()`, registered as
  `before_tool_callback` in `COMMON_AGENT_CALLBACKS` (every specialist agent) and explicitly on
  `root_agent` (which assembles its callback list separately from `COMMON_AGENT_CALLBACKS`).
- `docs/NOTIFICATIONS.md`: new "Tool calls in the CLI" section documenting the gap and the fix.
