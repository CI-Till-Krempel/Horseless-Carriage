# Issue

- Issue ID: ISSUE-0026
- Title: No Way to Notice New Work (New Commits, Stories Ready) Without Watching the Session
- Status: Done
- Priority: Could
- Owner: Architect
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #48): "Does the current processing only use one thread? Wouldn't it make more
sense to have the agents running in parallel, and depending on the roadmap board, check regularly
whether there are stories for them to process. When the tasks move into correct state (e.g. ready for
developers) the system should mechanically pick up the task and trigger the agent. As changes to the
roadmap could be small commits, the system could be notified, when the develop branch gets new
commits and then trigger any agents, that match the new state." The reporter's own follow-up comment:
"This is a big change, and should be configurable in the best case."

**On the threading question**: confirmed single-threaded/turn-based by design. `ScrumOrchestrator`
delegates to exactly one sub-agent at a time via ADK's `transfer_to_agent`, and essentially every tool
in `agents/scrum_team/tools/` mutates `tool_context.state` in place with no locking/transaction
semantics - `save_state_to_repo`'s own file write wasn't even atomic before ISSUE-0024. Actually
running multiple roles concurrently would require either giving every one of those tools real
concurrency safety, or a different state model entirely - a much larger change than fits alongside
the rest of this issue, and (per the reporter's own "should be configurable... big change" caveat)
not what this change attempts.

**On "mechanically pick up the task and trigger the agent"**: investigated whether this repo has any
existing mechanism for a fully headless, non-interactive agent turn that could be reused as a trigger.
It does - `agents/scrum_team/scripts/run_eval.py`'s ADK `InMemoryRunner` pattern, used by the
automated evaluation harness - but that pattern creates a disposable, ephemeral session specifically
for one harness run. Reusing it against this repo's real, persistent production session (the
sqlite-backed session, `sessions/adk_sessions.db`, that a live web/CLI user may have open at the same
moment) risks two writers racing on the same `session.state` - a genuine correctness problem, not
just an engineering inconvenience - and resolving that safely is its own focused piece of work, not
something to bolt on as a side effect of this change.

## Acceptance Criteria
- A clear, documented answer to "does this only use one thread" - yes, with the concrete reasons why
  (ADK's sequential agent-transfer model; in-place, non-thread-safe state mutation across nearly every
  tool) - rather than leaving it an open question.
- A new, entirely **optional, opt-in** script that polls for exactly the two things the issue calls
  out: new commits on the develop branch, and a story moved into a state another role is waiting to
  pick up (generalized beyond just "Ready" - any stage one short of the next in the pipeline). Nothing
  else in this repo imports or runs it; `run.py`'s own default behavior is completely unchanged. This
  is the "configurable" the reporter's own comment asked for.
- When either condition fires, the script notifies (a hard-to-miss console banner) rather than
  silently doing nothing - closing the actual gap the issue describes (a human has to remember to
  check for new work) - without attempting the higher-risk "auto-start a real session" behavior called
  out above as unsafe to bolt on here.
- Configurable poll interval (`WATCH_POLL_INTERVAL_SECONDS`, `.env`), defaulting to a sane value if
  unset or invalid.
- Degrades safely: no `STATE_REPO_PATH` configured, an unreachable git remote, a missing/corrupted
  `.hc/state.json`, or a `develop` branch with no prior recorded HEAD (first-ever check) all report
  "nothing to do" rather than crashing the poll loop or reporting a false trigger.

## Notes
- **Not attempted**: true concurrent/parallel agent execution, and automatically starting/driving a
  headless agent turn when new work is detected. Both are explained above and are real, valuable
  follow-ups - but both need dedicated design work (a concurrency-safe state model for the former; a
  session-safe headless-trigger mechanism, likely building on `run_eval.py`'s `InMemoryRunner`
  pattern but adapted to coordinate with a live production session, for the latter) that doesn't fit
  safely alongside this change. This mirrors the same reasoning already applied to entrypoint.sh's
  signal handling in ISSUE-0024 and GitHub App manifest automation in ISSUE-0023 - a big, explicitly-
  flagged-as-risky ask gets a scoped, safe first slice plus a clearly documented deferral, not a
  rushed full implementation. Filed as `specs/stories/EP-0008-Concurrency-Safe-State-And-A-Working-
  Parallel-Loop.md`, prompted directly by a PR review comment proposing git-commit-based state updates
  as the fix - that part (atomic writes + a commit per save) is already done via ISSUE-0024; EP-0008
  breaks down what's still needed on top of it (optimistic concurrency, session locking) before a
  headless trigger or real concurrent execution can be safe.
- `watch_roadmap.py` deliberately does not import anything from `agents/` (the ADK/pydantic-dependent
  package) - it's a plain host-side script, stdlib-only, in the same style as `doctor.py`/
  `lib_docker.py`, so it doesn't need `requirements.txt` installed to run.
- Where the gap lived: no code anywhere in this repo watched the develop branch or backlog stage
  transitions at all - the only way to know "is there new work" was for a human to look.

## Test Approach
- `tests/test_watch_roadmap.py` - `count_stories_ready_for_next_stage` (Ready-but-not-Implemented and
  Reviewed-but-not-Tested both count, a fully-Accepted story doesn't, missing/corrupted/non-dict
  state.json all return 0, counts across both backlog lists); `develop_branch_head` (returns the
  stripped sha, empty string on `rev-parse` failure or any exception - never raises);
  `check_once` (no `STATE_REPO_PATH` never triggers, first-ever check with an already-known HEAD
  doesn't falsely report "new commits", new commits since the last check trigger + notify, an
  unchanged HEAD doesn't trigger, a ready story triggers even with no new commits, an unreachable
  remote degrades to "no new commits detected" without crashing or losing the last-known-good head);
  `main` (`--once` exit code reflects whether anything triggered, an invalid poll interval falls back
  to the default).
- `pytest tests/` (full host-side suite): 235 passed, no regressions.

## Resolution
- `watch_roadmap.py` (new): `count_stories_ready_for_next_stage`, `develop_branch_head`, `check_once`,
  `main` - see module docstring for the full design/scoping rationale.
- `docs/RUNNING.md`: new "Watch Mode: Get Notified of New Work" section documenting both the
  threading-model answer and the new script.
- `.env.example`: documents `WATCH_POLL_INTERVAL_SECONDS`.
- `README.md`, `docs/SETUP.md`: added to the host-script listings.
