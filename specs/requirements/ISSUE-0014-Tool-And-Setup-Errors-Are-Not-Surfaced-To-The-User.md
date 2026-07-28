# Issue

- Issue ID: ISSUE-0014
- Title: Tool and Setup Errors Are Not Surfaced to the User for Resolution
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-28

## Overview
Several tools already return a structured `{"status": "error", "message": "..."}` result on failure
(e.g. `configure_github_repo`/`configure_github_app`/`seed_repository` in
`agents/scrum_team/tools/github.py`/`docs.py`, or a rejected `advance_story_stage`/
`plan_sprint_backlog_item` call) - and `on_tool_error_callback`
(`agents/scrum_team/agent.py`) already turns a hallucinated/out-of-role tool call into a similar
error result instead of crashing the run. But nothing in `ORCHESTRATOR_PROMPT` ever required the
model to actually relay one of these error results to the human and ask for what's needed to resolve
it. Nothing stops the model from silently retrying, quietly dropping the requested action, or
responding as if the call had succeeded - especially likely for setup-stage failures
(`configure_github_repo` failing because of a bad URL, `configure_github_app` failing on a malformed
key), which block everything downstream but are easy for a model under budget pressure to gloss over
in favor of a plausible-sounding narrated response (see ISSUE-0012 for the closely related failure
mode of narrating instead of acting).

## Acceptance Criteria
- `ORCHESTRATOR_PROMPT` states explicitly that any tool result with `status: "error"` (the
  orchestrator's own, or one relayed after a sub-agent's failure) must be surfaced to the user in the
  next response: what failed, the tool's own error message, and what's needed from the user to
  resolve it.
- The instruction explicitly forbids silently retrying, dropping the action, or responding as if the
  call succeeded.
- Setup/configuration failures are called out specifically as always worth a message, since they
  block all downstream work.

## Notes
- Where the gap lived: `ORCHESTRATOR_PROMPT` had no instruction at all governing what to do with an
  error-status tool result - `on_tool_error_callback` (`agents/scrum_team/agent.py`, "Tool Dispatch
  Error Handling") already turns a crash into a normal tool-response event the calling agent *can*
  react to, but nothing required it to actually surface that reaction to the human.
- Complements ISSUE-0012/0013: those fixes make the orchestrator more likely to actually attempt
  tool calls and delegate; this issue ensures that when one of those calls fails, the user finds out
  and can act on it, rather than the failure being silently absorbed.

## Test Approach
- Prompt-behavior fix, verified by re-reading the edited section against the acceptance criteria
  above (see ISSUE-0012's Test Approach for the same reasoning - not independently unit-testable the
  way a tool's return value is; the underlying error-status contract on individual tools like
  `configure_github_repo`/`configure_github_app` is already covered by their own existing tests in
  `agents/scrum_team/tests/test_github.py`).

## Resolution
- Added an "ERRORS ARE REPORTED, NEVER SWALLOWED" section to `ORCHESTRATOR_PROMPT`
  (`agents/scrum_team/prompts.py`), immediately after the expanded FIRST MESSAGE SUMMARY: requires
  surfacing any `status: "error"` tool result (what failed, the tool's own message, what's needed
  from the user), explicitly forbids silent retries/dropped actions/false-success responses, and
  calls out setup/configuration failures specifically as always worth a message.
