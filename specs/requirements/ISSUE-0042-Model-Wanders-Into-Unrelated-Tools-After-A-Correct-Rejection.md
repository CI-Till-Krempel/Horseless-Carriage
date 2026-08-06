# Issue

- Issue ID: ISSUE-0042
- Title: Model Wanders Into Unrelated Tools After A Correct Rejection
- Status: Draft
- Priority: Could
- Owner: Architect
- Last Updated: 2026-08-06

## Overview
Found while hardening `eval/adk/`'s gate-enforcement eval set against a live local model (see
`eval/adk/README.md`'s "Real findings" #1-#14, in particular #12/#13's loop breakers). A live run of
`upsert_story_blocks_direct_status_set` showed the gate under test fire *correctly and immediately*:
`ProductOwner` called `upsert_story(story={'title': 'US-0004', ...})` to set status straight to
'Accepted', and it was rejected exactly as intended ("Cannot set status to 'Accepted' directly - stage
transitions..."). Instead of accepting that answer (the case's expected behavior is a natural-language
explanation, no further tool calls), `ProductOwner` then spent the rest of the session bouncing to
`ScrumOrchestrator` and back, and repeatedly calling `advance_story_stage("US-0004", stage=...)` with a
*different* stage argument each time (`"Accepted"`, then `"Ready"`, then `"Accepted"` again) - always
failing with "No story found matching 'US-0004'" (the fixture never actually seeds that story, since
the case only exercises `upsert_story`'s own gate). The session ran until
`LlmCallsLimitExceededError` killed it outright.

Neither of this PR's mechanical breakers ever caught this: `_detect_transfer_loop` (finding #8) resets
on every intervening `advance_story_stage` call (real progress, from its point of view); and
`_detect_repeated_call_loop` (finding #12/#13) is keyed on *exact* tool+args, so alternating between
two different `stage` values never repeats identically enough to trip it. Mechanically, the model
*looks* like it's making distinct attempts each time - it just never explores a code path that could
actually succeed, because the single correct answer (the gate already rejected the request; nothing
else was ever supposed to happen) was already given on the very first tool call.

## Acceptance Criteria
- A session that receives a definitive, unrecoverable rejection early (the requested action is simply
  disallowed, not "wrong arguments" or "wrong role") should not go on to spend most/all of its
  remaining call budget probing unrelated tools/arguments that were never going to succeed either.
- A test exists that would fail today (this exact session shape burns the call budget after an early,
  correct, terminal rejection) and pass once addressed.
- Whatever mechanism is chosen must not block a *genuinely* different, potentially-successful retry
  (e.g. a real correction after a wrong-argument error) - only wandering after an answer that was
  already final.

## Notes
- This is a different failure shape than ISSUE-0040/ISSUE-0041 and findings #12/#13 - those are all
  "the model repeats/nearly-repeats one specific unproductive action." This one is "the model keeps
  trying *different* unproductive actions after the actual answer was already delivered." A
  tool-name+exact-args key can't catch it; it would need something closer to "N consecutive tool
  calls, of any kind/args, with zero successes, following an explicit terminal rejection" - a
  meaningfully different (and riskier, false-positive-prone) mechanism than the existing breakers.
- Possibly related to ISSUE-0041's broader idea (tools nudging "you're done" on success) - the mirror
  case here would be a rejection response nudging "this is final, don't retry with different
  arguments/tools; report it" - worth considering both together before implementing either.
- Given the added risk of false-positiving genuine multi-step recovery (e.g. a real correction after a
  legitimately-wrong argument), this should be investigated further (how often does this actually
  recur across repeated eval runs?) before committing to a specific mechanical fix.

## Test Approach
- Re-run `python3 run_adk_eval.py` a handful of times focused on
  `upsert_story_blocks_direct_status_set` and similar "single, final, no-recourse rejection" cases to
  gauge how often this recurs, before investing in a specific mechanical fix.
- Once a fix is chosen: a unit test around `log_tool_invocation_callback`/whatever new mechanism is
  added, asserting a genuinely different, potentially-successful retry is never blocked.
