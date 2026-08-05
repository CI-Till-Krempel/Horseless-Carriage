# Issue

- Issue ID: ISSUE-0040
- Title: Orchestrator Misroutes An Explicitly-Named Target Agent
- Status: Draft
- Priority: Should
- Owner: Architect
- Last Updated: 2026-08-05

## Overview
Found while hardening `eval/adk/`'s gate-enforcement eval set against a live local model (see
`eval/adk/README.md`'s "Real findings" #1-#12). A live run's prompt was "DevTeam, please start
implementing story US-0007 now." - the user named `DevTeam` explicitly, as the very first word. The
transcript showed `ScrumOrchestrator` calling `transfer_to_agent(agent_name="ProductOwner")` instead -
a different role than the one named. The eval case in question
(`sub_agent_blocked_without_budget_capped_virtual_key`) exists to test the missing-LiteLLM-key
refusal, and that refusal itself fired correctly - just for the wrong agent (`"Agent 'ProductOwner'
has no LiteLLM virtual key yet..."` instead of `'DevTeam'`), which is what actually failed the case's
exact-match assertion.

This is a routing-fidelity gap, not a missing gate: `ORCHESTRATOR_PROMPT` (`prompts.py`) already
instructs the Orchestrator to delegate to the named specialist, but nothing mechanical verifies the
transfer target actually matches an agent name the user's own message named. Same underlying class of
problem as ISSUE-0039 (a weak local model doesn't reliably follow an instruction that's only stated in
the prompt) - that issue's own conclusion applies here too: hardening against the observed *symptom*
is the robust fix, not relying on the model to self-correct once told.

## Acceptance Criteria
- When a user message begins with an explicit role prefix that names one of the six specialist agents
  (`ProductOwner,` / `ScrumMaster,` / `DevTeam,` / `QA,` / `Architect,` / `QualityGuardian,` - matching
  how every eval-set prompt and this repo's own real usage already addresses a role), the Orchestrator
  must actually transfer to that exact agent, not a different one, with high reliability across
  repeated runs against a live model.
- A test exists that would fail today (an explicitly-named target agent doesn't receive the transfer)
  and pass once fixed.
- Whatever fix is chosen must not require the model to get this right unassisted every time - a
  mechanical check (verifying/forcing the transfer target against the named agent) is preferred over a
  prompt-only reminder, consistent with ISSUE-0039's resolution.

## Notes
- Root cause lives in model routing behavior, surfaced through `ORCHESTRATOR_PROMPT`
  (`agents/scrum_team/prompts.py`) and `log_tool_invocation_callback`/`transfer_to_agent` dispatch
  (`agents/scrum_team/agent.py`) - there is currently no code path that reads the *user's own message*
  to sanity-check a transfer's target against it.
- Distinct from the self-transfer guard (`log_tool_invocation_callback`'s `target_agent == agent_name`
  check) and the loop breakers (`_detect_transfer_loop`, `_detect_repeated_call_loop`) - this is a
  single wrong-but-otherwise-well-formed transfer, not a repeated/self one, so none of those catch it.
- Only reproduced once so far (one eval run, one case) - worth confirming it's reliably reproducible
  (re-run the eval a few times, or add a dedicated eval case) before investing in a mechanical fix, in
  case it turns out to be rarer than the loop/self-heal problems this same PR fixed.

## Test Approach
- A new `eval/adk/scrum_team.evalset.json` case (or a dedicated unit test around
  `log_tool_invocation_callback`/whatever mechanism the fix adds) with an explicit-role-prefixed
  prompt, asserting the transfer target matches the named agent.
- Re-run `python3 run_adk_eval.py` a handful of times to check how often this actually recurs before
  deciding how aggressive the fix needs to be.
