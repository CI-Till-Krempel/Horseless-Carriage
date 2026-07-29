# Issue

- Issue ID: ISSUE-0029
- Title: Conversation History Duplication Bug, and No Detection When the Orchestrator Stops Acting
- Status: Done
- Priority: Must
- Owner: Architect
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #70): a real session got stuck - the user gave a clear sprint goal and said
the repo/budget were already configured, but the Orchestrator kept re-describing the same three-item
plan ("1. Define the Sprint Goal 2. Set a Budget 3. Configure the Repository") turn after turn,
without ever calling a single tool. `Repository: Not configured` stayed stuck at that value across
the entire session even though the user insisted config was done. The attached session transcript
also showed the model's own replies as raw, JSON-stringified text
(`{"role": "assistant", "content": "..."}`) rather than natural language, and rapidly growing token
usage per turn (2,099 -> 4,264 -> 6,438 -> 8,583).

**Root cause, found in the transcript itself**: `state["messages"]` (the flat, human-readable
conversation history) was duplicating entire past exchanges on every single turn. Turn 2's history
included the *entire* turn 1 exchange twice; turn 3's included turns 1-2 duplicated multiple times
over. This is why: `google.adk.flows.llm_flows.contents._ContentLlmRequestProcessor` already replays
the full conversation from `invocation_context.session.events` into `llm_request.contents` by
default, *before* any `before_model_callback` runs - so on any normal, continuing session,
`llm_request.contents` already has the real history by the time our own code sees it.
`history_management_callback`'s "resume" step nonetheless re-prepended the entire persisted
`state.messages` on top, gated on `not llm_request.previous_interaction_id` - which is falsy on the
first internal model call of *every new user turn* (a fresh ADK "invocation"), not just a session's
true first turn ever. The very next line in the same function ("sync") then wrote this now-doubled
`llm_request.contents` straight back into `state.messages` - so the *next* turn's already-longer
ADK-native history got a fresh `state.messages` replay stacked on top again, compounding without
bound. A context stuffed with several copies of the same old exchanges would plausibly both bury the
user's actual latest instruction under noise and burn through the model's usable context budget -
either of which could produce exactly the observed "stuck describing the same plan" behavior, and the
inflated token counts match precisely.

Separately, nothing made this - or any other case of the Orchestrator "just talking" instead of
acting - visible to a human without them reading every reply closely, and nothing forced a
proactively visible error/warning when it happened.

## Acceptance Criteria
- `history_management_callback`'s recovery-injection only fires when ADK's own native history is
  itself empty (i.e. a genuinely fresh session recovering from a persisted `state.messages` - the one
  case it's actually needed for), never when `llm_request.contents` already reflects a real,
  continuing conversation - so the entire history is never duplicated again.
- The Orchestrator's own turns are tracked for whether they included an actual tool call. After
  `ORCHESTRATOR_STALL_THRESHOLD` (3) consecutive replies with none, a hard-to-miss `⏸ [NO ACTION
  TAKEN...]` banner is mechanically prepended to the model's own visible response text - not merely a
  prompt instruction it might not follow - and a blocking interaction is recorded (GH issue
  #53/ISSUE-0025), so it's visible both in the chat itself and to anything watching
  `list_blocking_interactions()`/the notifier plugin system.
- A real tool call (including `transfer_to_agent`, itself a tool call) resets the streak - genuine
  delegation is exactly the "acting, not just talking" behavior this exists to confirm.
- `ORCHESTRATOR_PROMPT` explicitly instructs a self-check before replying: if the draft response is a
  plan/checklist rather than the result of an already-made tool call, when the user asked for action,
  go make the call first - and explicitly names the observed failure pattern (re-listing the same
  numbered plan turn after turn) as the clearest sign of being stuck in it.

## Notes
- **Not attempted**: a fix for the raw JSON-stringified response text observed in the transcript
  (`{"role": "assistant", "content": "..."}`). This is much more likely a symptom of the context-
  duplication bug above (a model confused by a bloated, repetitive context falling into an unusual
  output pattern) than a separate, independent bug - no code in this repo constructs or expects that
  wrapper shape anywhere, so there's no known code-level cause to point at directly. Revisit if it
  recurs after this fix.
- `orchestrator_stall_count` is deliberately **not** added to `REPO_STATE_KEYS` (`agents/scrum_team/
  tools/scrum.py`) - it's a live-session-only signal ("has *this* running conversation gone quiet"),
  not a real sprint artifact worth persisting to `.hc/state.json`/git history.
- The user's own question - "is the current implementation broken, or do we need another approach
  (like the parallel loop epic)?" - the history-duplication bug is exactly the kind of concrete,
  fixable implementation defect that should be ruled out before concluding a bigger architectural
  change (EP-0008) is actually needed for this specific symptom.

## Test Approach
- `agents/scrum_team/tests/test_history.py::test_no_injection_when_adk_already_supplied_native_history` -
  the exact regression scenario: `llm_request.contents` already has multiple entries (simulating
  ADK's own native replay) - the old code would have duplicated the whole thing on top; the fixed
  code leaves it untouched.
- `test_recovers_history_into_a_genuinely_fresh_session` - confirms the one case the injection logic
  exists for (empty native contents, non-empty `state.messages`) still works.
- `test_orchestrator_stall_count_increments_without_a_tool_call`,
  `test_a_real_tool_call_resets_the_stall_count`,
  `test_stall_threshold_prepends_a_visible_banner_and_records_blocking_interaction`,
  `test_stall_banner_only_fires_once_at_the_threshold_not_every_turn_after`,
  `test_stall_tracking_does_not_apply_to_specialist_agents` - the new stall-detection mechanism.
- Full `agents/scrum_team/tests` suite (via `docker compose --env-file .env.test run --rm
  --entrypoint "" -e PYTHONPATH=/app agent pytest --cov=agents agents/scrum_team/tests`): 228 passed,
  no regressions.
- `pytest tests/` (host-side suite, unaffected): 274 passed.

## Resolution
- `agents/scrum_team/agent.py`:
  - `history_management_callback`: recovery-injection gate changed from `not
    llm_request.previous_interaction_id` to `len(llm_request.contents) <= 1` (only inject when ADK's
    own native history is itself empty).
  - New `ORCHESTRATOR_STALL_THRESHOLD` constant and `_track_orchestrator_stall()` helper, called from
    `history_management_after_callback` for the Orchestrator specifically - tracks consecutive
    tool-call-free replies, mechanically prepends the visible warning banner and records a blocking
    interaction once the threshold is hit, resets on any real tool call.
- `agents/scrum_team/state.py` / `agents/scrum_team/tools/scrum.py`: added
  `orchestrator_stall_count: int = 0` (not part of `REPO_STATE_KEYS` - see Notes).
- `agents/scrum_team/prompts.py`: `ORCHESTRATOR_PROMPT`'s `DELEGATION IS MANDATORY, NOT DESCRIPTIVE`
  section gained a `SELF-CHECK BEFORE REPLYING` bullet describing the exact failure pattern observed
  and what to do about it, including recognizing the mechanical stall banner if one appears.
- `MANUAL.md` § Troubleshooting: documents the new banner for end users.
