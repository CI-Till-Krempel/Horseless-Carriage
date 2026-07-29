# Issue

- Issue ID: ISSUE-0039
- Title: Model Emits Fake Text "Tool Calls" Instead Of Real Ones
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #89): "Scrum Orchestrator not working in Web UI" - the user started a fresh
web-UI session, had to send a message themselves before anything happened, and the Orchestrator then
appeared stuck: every single reply for 10 straight turns (across "Hi", "Who are you?", "Lets start
the sprint", setting a sprint goal, etc.) was unresponsive to what was actually asked.

**Root cause, found from the attached session export**: every one of those "replies" was plain TEXT
shaped exactly like a tool call - e.g. `{"type": "function", "function": "repo_status", "arguments":
{}}` or `{"type": "function", "function": "update_sprint_goal", "arguments": {"goal": "..."}}` - not
a real ADK `function_call` part. Confirmed independently by the session's own
`orchestrator_stall_count` climbing to 8 and the `⏸ [NO ACTION TAKEN...]` banner (ISSUE-0029/GH issue
#70) firing at turn 3 exactly as designed - that detector already correctly recognizes these turns as
"no real tool call made," but its text-only warning didn't reliably get the model (`gemini-1.5-pro`,
per `litellm.yaml`, routed through the LiteLLM proxy under the `scrum-orchestrator` alias) to actually
self-correct in this real session; it kept doing the same thing 5 more times after the warning.
`prompts.py`'s `DELEGATION IS MANDATORY, NOT DESCRIPTIVE` section already explicitly names this exact
failure mode ("an improvised JSON blob") as something to avoid - a prompt-side guardrail that clearly
isn't sufficient alone.

Two smaller findings from the same investigation:
- `update_sprint_goal` is not a real tool name anywhere in this codebase - the real tool is
  `start_sprint(goal, tool_context=None)`, and it belongs to Scrum Master, not the Orchestrator. The
  model both hallucinated a nonexistent tool and, had it been real, targeted the wrong agent.
- The "no greeting on session create" part of the report is expected ADK behavior, not a defect this
  repo can fix: there is no code path anywhere in this repo, nor in ADK's own web/CLI frontends, that
  invokes an agent before the user sends a first real message - `MANUAL.md` previously claimed
  otherwise ("you never have to send a message just to get it to engage"), which was simply
  inaccurate and is corrected here.

## Acceptance Criteria
- A model reply that is plain text shaped exactly like `{"type": "function", "function"|"name":
  "<tool>", "arguments"|"args": {...}}` is converted into a real ADK `function_call` part
  mechanically - not dependent on the model changing its own behavior - so the intended action
  actually runs (including going through the existing "tool not found" recovery if the name turns
  out to be hallucinated, exactly as if the model had made a real, malformed tool call).
- This must never touch a genuine prose reply, even one that happens to be valid JSON or discusses a
  tool by name - only an exact, whole-string match against this precise shape triggers the recovery.
- Applies to every agent (all six specialists + the Orchestrator), since this is a model-level output
  quirk, not specific to one role.
- Integrates cleanly with the existing stall detector (ISSUE-0029): once recovered into a real
  function_call, the stall streak resets, since the model's actual intent *was* to act.
- `MANUAL.md`'s inaccurate "never have to send a message" claim is corrected to describe what
  actually happens (a first real user turn is still required; the *reply* to it is the rich,
  state-aware greeting).

## Notes
- Deliberately a **mechanical backstop**, not an attempt to fix the model's behavior or swap the
  underlying model - this repo doesn't control why a given model occasionally does this (weak
  function-calling adherence on an older/cheaper model, prompt-following limitations, LiteLLM-proxy
  translation fidelity - all plausible, none confirmed as *the* cause), so hardening the system
  against the observed *symptom* is the robust fix regardless of the underlying reason.
- Implemented as an in-place mutation of `llm_response.content.parts` (matching
  `_track_orchestrator_stall`'s established pattern), not a newly-constructed `LlmResponse` - ADK's
  own flow (`base_llm_flow.py`'s `_handle_after_model_callback` -> `_postprocess_async`) re-checks
  the *same* response object for `function_call` parts after every `after_model_callback` runs and
  dispatches them through its normal machinery, so this recovers the model's intent as a genuinely
  real tool call rather than reimplementing dispatch here.
- Registered *before* `update_token_usage_callback`/`history_management_after_callback` in the
  `after_model_callback` chain, so the stall detector and transcript recording both see the corrected
  response, not the raw fake-JSON text.

## Test Approach
- `agents/scrum_team/tests/test_agent.py::TestRecoverFakeToolCallCallback` - converts the exact
  shape (both `function`/`arguments` and `name`/`args` key spellings); does not touch a real
  `function_call`, legitimate prose, unrelated JSON, or a response with multiple text parts; handles
  empty content without raising; integrates correctly with the stall detector (recovered call resets
  the streak); registered on every agent including the Orchestrator.
- Full `agents/scrum_team/tests` suite (via `docker compose --env-file .env.test run --rm
  --entrypoint "" -e PYTHONPATH=/app agent pytest --cov=agents agents/scrum_team/tests`): 275 passed,
  no regressions.
- `pytest tests/`: 302 passed, no regressions.

## Resolution
- `agents/scrum_team/agent.py`: new `recover_fake_tool_call_callback()`, registered first in
  `COMMON_AGENT_CALLBACKS["after_model_callback"]` (every specialist agent) and explicitly on
  `root_agent` (which assembles its own callback list separately).
- `MANUAL.md` § 4 and § 8 (Troubleshooting): corrected the "never have to send a message" claim;
  added a troubleshooting entry for the fake-tool-call-shaped-text symptom.
