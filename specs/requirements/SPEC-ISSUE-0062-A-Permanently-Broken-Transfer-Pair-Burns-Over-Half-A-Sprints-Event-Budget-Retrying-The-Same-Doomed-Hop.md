# Issue

- Issue ID: SPEC-ISSUE-0062
- Title: A Permanently-Broken Transfer Pair Burns Over Half A Sprint's Event Budget Retrying The Same Doomed Hop
- Status: Ready
- Priority: Must
- Owner: Architect
- Last Updated: 2026-10-14

## Overview
Reported (`0.1.0-run68`'s `report.md`): the run stopped early after all 5 requested sprints completed
- `Stop reason: blocked_needs_human`, `Blocked story: US-0002 - product: ScrumOrchestrator and
ProductOwner bounced transfer_to_agent 3x with no progress`. The stop itself is the harness working
exactly as designed (`_sprint_needs_human_this_harness_cannot_provide`, `agents/scrum_team/scripts/
run_eval.py`) - a "product"-category blocker genuinely does need a human in real deployment, and this
scripted driver correctly has none to give it. The gap is in how much of Sprint 5's budget was spent
reaching that already-foregone conclusion.

**Confirmed mechanism.** `_detect_transfer_loop` (`agents/scrum_team/agent.py:1974-2058`) correctly
broke the ScrumOrchestrator/ProductOwner ping-pong on US-0002 the first time, at Sprint 5's Event 32
(`transcript.md:5614-5619`): 3 identical hand-offs in a row trips `TRANSFER_LOOP_THRESHOLD`, which
raises a real story blocker and remembers the pair in `_broken_transfer_pairs` - by design (GH issue
#367), **every further hop between that exact pair is refused again immediately, forever, for the rest
of the session** (`agent.py:2050-2058`). This part is correct and intentional.

What happens next is not: nothing stops either agent from simply retrying the identical,
permanently-doomed hop anyway. From Event 36 through Event 84 of Sprint 5 - **49 of the sprint's 85
total events (58%)** - ScrumOrchestrator and ProductOwner called `transfer_to_agent` on each other **21
more times**, each one refused with the exact same message: *"🔁 [TRANSFER LOOP DETECTED]
ScrumOrchestrator <-> ProductOwner already broke this exact loop once this session - refusing again
immediately..."* (`transcript.md:5648` et seq., confirmed via `grep -c "already broke this exact loop
once this session" transcript.md` = 21). Neither agent ever called a different tool, transferred to a
third role, or explained the blocker in plain text, despite the rejection message explicitly suggesting
all three. The sequence only ended at Event 85 when ScrumOrchestrator finally produced a closing text
summary instead of another tool call (`transcript.md:6056` onward) - not because anything mechanical
intervened.

This is a confirmed **guardrail gap**, not just agent behavior under a working guardrail: the breaker's
own refusal is correct and unconditional, but nothing escalates or changes consequence on repeat -
every one of the 21 extra attempts gets the identical static rejection text, with no stronger
intervention and no earlier exit. `_mark_blocker_mechanically_detected`'s own docstring (`agent.py:
1942-1961`) already states the intent: *"The eval harness... treats this as an immediate-stop signal:
a loop breaker firing only happens after the team already proved itself unable to make progress through
repeated attempts, unlike an ordinary blocker."* But the actual stop-check,
`_sprint_needs_human_this_harness_cannot_provide` (`run_eval.py:513-567`), only ever runs **after** a
sprint's `async for event in runner.run_async(...)` loop (`run_eval.py:708`) finishes on its own - via
`max_events_reached`, `max_duration_exceeded`, a produced sprint report, or (as here) the model simply
stopping on its own. "Immediate" in the docstring describes the harness's *decision* once it finally
checks, not *when* it checks - there is no mid-sprint early-exit for this exact, already-provably-dead
pattern, so the sprint's existing per-event loop (which already supports breaking early - see its
`if len(events) >= max_events or time.monotonic() >= deadline: break`, `run_eval.py:735-736`) had no
reason to stop 49 events sooner than it did.

## Acceptance Criteria
- `_run_one_sprint` (`agents/scrum_team/scripts/run_eval.py`) tracks, within its own per-event loop, how
  many times a `transfer_to_agent` tool response this sprint has contained `_detect_transfer_loop`'s
  "already broke this exact loop once this session" marker text - once that count reaches a small cap
  (comparable to `TRANSFER_LOOP_THRESHOLD`/`_DOMINANT_REJECTION_MIN_COUNT`'s existing conventions, not
  21), the sprint's turn ends early with a new, clearly-named `stop_reason` (e.g.
  `transfer_loop_exhausted`) - the same way `max_events_reached`/`max_duration_exceeded` already do.
- This must not change the actual stop-or-continue decision for the overall run -
  `_sprint_needs_human_this_harness_cannot_provide` still runs on the (now earlier-terminated)
  `sprint_result` exactly as before and makes the same call it always would have; this only stops
  burning further events reaching that already-foregone conclusion.
- A new regression test: a mocked event stream where the same already-broken-pair rejection message
  repeats past the cap - assert `_run_one_sprint` returns with far fewer than 85 events and the new
  `stop_reason`, not by running out `max_events`/`max_duration`/the model's own choice to stop.
- Does not touch `_detect_transfer_loop`'s own behavior (`agent.py`) at all - the pair-breaking logic
  itself is correct and intentional (GH issue #367); this is purely a harness-side budget backstop for
  the case where the team keeps hitting it anyway.

## Notes
- Distinct from every prior loop-related issue this session (SPEC-ISSUE-0053's loop-breaker
  misattribution, SPEC-ISSUE-0060's unverified-resolution gap): both of those were about a breaker
  attaching to the wrong target or a resolution not being re-checked. This is about what happens
  *after* a breaker has already permanently and correctly closed off one specific path - nothing
  stops the team from walking into that closed door repeatedly.
- `_dominant_repeated_rejection` (`run_eval.py:574-611`, GH #409) is a close sibling mechanism but
  checks the *completed* `sprint_result` across a *sprint boundary* (comparing this sprint's dominant
  rejection to the previous sprint's) - it would not have caught this, since Sprint 5 only had one
  sprint's worth of repeats and the run stopped (correctly) before a second sprint's comparison could
  ever happen. This issue's fix is a true mid-sprint, single-invocation backstop, not a cross-sprint one.
- The underlying story (US-0002) blocking on a genuine product-category question is itself fine and
  expected in a scripted, human-free eval - not something to "fix" away. Only the wasted retry budget
  getting there is the gap.

## Test Approach
- `agents/scrum_team/tests/test_run_eval.py` (wherever `_run_one_sprint` is tested): construct a fake
  `runner.run_async` async generator that yields a `transfer_to_agent` error event containing
  `_detect_transfer_loop`'s "already broke this exact loop once this session" marker repeatedly (more
  than the new cap) with no other tool calls in between - assert `_run_one_sprint` returns with
  `stop_reason == "transfer_loop_exhausted"` and an `event_count` well below both `max_events` and the
  total the fake generator could have produced.
- A second case: the same marker appears fewer than the cap times, interspersed with other real tool
  calls/responses - assert the sprint runs to its normal conclusion, unaffected (this is a budget
  backstop, not a hair-trigger).
