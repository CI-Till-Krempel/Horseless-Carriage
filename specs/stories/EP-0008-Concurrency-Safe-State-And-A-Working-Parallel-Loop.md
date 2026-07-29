# Epic

- Epic ID: EP-0008
- Title: Concurrency-Safe State and a Working Parallel Loop
- Status: Draft
- Priority: Should
- Owner: Architect
- Last Updated: 2026-07-29

## Overview
Follow-up to GH issue #48 (ISSUE-0026, `watch_roadmap.py`) and a review comment on its PR: "We could
fix the mutual write problem by using git for the updates to the state, no? ... If we change the
tooling around the roadmap to use atomic git commits instead of raw writes, that might make the
updates more robust and traceable. Let's create an epic for this parallel execution topic and fill in
the tasks/stories needed to have a working parallel loop in place."

**Worth clarifying up front**: atomic writes + git-commit checkpoints on every state save already
exist (ISSUE-0024, `agents/scrum_team/tools/scrum.py`'s `_write_state_atomically`/
`_checkpoint_state_commit`) - that part of the suggestion is done. What that change does *not* do is
solve the actual "mutual write" problem ISSUE-0026 flagged: `save_state_to_repo()` writes the calling
process's **entire current in-memory snapshot**, unconditionally, every time - not a diff, not a
merge. If two live processes (say, a human's interactive session and a headless-triggered one) each
hold their own in-memory copy of `sprint_backlog`/`impediment_log`/etc. and both eventually save,
whichever saves *second* completely overwrites the first's unique changes with its own stale-relative-
to-those-changes snapshot. Git's commit history preserves both versions for forensic purposes (you can
see it in `git log`), but nothing today stops the silent data loss from happening in the first place -
a git commit is a checkpoint, not a lock or a merge.

Getting from "an optional notifier" (`watch_roadmap.py` today) to "the system mechanically picks up
ready work and processes it, possibly with real concurrency" - the actual GH issue #48 ask - needs
that gap closed first, plus a real decision on the execution model. This epic breaks that down.

## User Stories / Features
Proposed, not yet filed as individual stories - roughly in dependency order (each later story assumes
the ones before it exist):

- **Optimistic-concurrency state writes** - `save_state_to_repo()` records the git commit sha it last
  loaded from (or last wrote); before writing, check whether `HEAD` for `.hc/state.json` has moved
  since then. If it has, don't blindly overwrite - reload the latest checkpoint and either (a) merge
  the caller's own delta on top per-key (most `REPO_STATE_KEYS` are append-only lists - safe to
  concatenate/dedupe rather than replace), or (b), where a clean merge isn't obvious, refuse the write
  and surface it as a blocking interaction (GH issue #53/ISSUE-0025 - "state changed underneath you,
  reconcile before continuing") rather than silently discarding one side. This is the actual mechanism
  that turns "we commit to git" into "concurrent writers are safe", which plain commits alone aren't.
- **Session/process locking** - a lightweight advisory lock (e.g. a `.hc/session.lock` file recording
  PID + start time) so two live ADK sessions never run business logic against the same
  `STATE_REPO_PATH` at the same time in the first place. This is the cheaper, more robust alternative
  to "make every tool safe under true concurrency" for the common case (a human's session + a
  headless-triggered one) - most conflicts are best avoided rather than merged. `doctor.py` gains a
  check that reports a stale lock (process no longer running) as a warning, so a killed session
  doesn't permanently block the next one from starting.
- **Headless trigger session** - once the two stories above make it safe to do so, `watch_roadmap.py`
  (or a successor) actually starts a real, non-interactive agent turn when it detects new work,
  instead of only printing a notification - adapting `agents/scrum_team/scripts/run_eval.py`'s ADK
  `InMemoryRunner` pattern to attach to the real production session (guarded by the lock above) rather
  than a disposable one-off session. This is the literal "mechanically pick up the task and trigger
  the agent" from GH issue #48, unblocked by the state-safety work above.
- **Resume-after-interruption via git** - if a live session (interactive or headless-triggered) is
  killed mid-turn, the next session/trigger reloads via `load_state_from_repo`'s existing git-recovery
  path (ISSUE-0024) *and* the optimistic-concurrency check above, so it picks up from the last real
  checkpoint rather than assuming the ADK sqlite session db (`sessions/adk_sessions.db`) survived
  intact.
- **Real concurrent sub-agent execution (the "parallel loop" itself)** - the literal "have the agents
  running in parallel" ask: e.g. Architect reviewing story A while Dev Team implements story B,
  instead of one role at a time via `transfer_to_agent`. The highest-risk, most speculative story here
  by a wide margin - it needs all four stories above as a prerequisite foundation (state safety,
  locking, headless triggering, resume), plus its own spike to decide the actual execution model
  (separate processes per role each with their own Runner/session coordinating through the
  git-checkpointed state above, vs. one process running async/threaded sub-Runners) before any
  implementation is scoped. Do not skip straight to this story - the foundation is what makes it safe.

## Acceptance Criteria
- Two processes independently calling `save_state_to_repo()` against the same state repository no
  longer silently discard one side's changes - either a correct merge happens, or the conflict is
  surfaced as a blocking interaction, but data loss is never silent.
- A human's interactive session and `watch_roadmap.py`'s headless trigger (once implemented) never run
  business-logic tool calls against the same state repository concurrently - the lock, not luck,
  prevents it.
- `watch_roadmap.py` (or its successor) can actually start a real sprint-processing turn when it
  detects new work, not just print a notification - the deferred half of GH issue #48/ISSUE-0026.
- The final "real concurrent execution" story explicitly requires its own spike/decision output before
  implementation work is scoped - this epic does not commit to a specific concurrency model up front.

## Notes
- **This directly builds on, and clarifies, ISSUE-0024** (state.json checkpointing, GH issue #59) -
  see the Overview above for exactly what that change already solved (atomic writes, a git history to
  recover from) versus what it didn't (concurrent-write safety, which needs the optimistic-concurrency
  story above).
- **This directly builds on ISSUE-0026** (`watch_roadmap.py`, GH issue #48) - that change deliberately
  stopped at "notify a human" specifically because the concurrency-safety work in this epic didn't
  exist yet; see its Notes section for the original reasoning.
- Also relates to GH issue #53/ISSUE-0025 (`blocking_interactions`) - the optimistic-concurrency
  story's conflict-surfacing path is a natural new use of that same mechanism, not a new one.
- Most `REPO_STATE_KEYS` (`impediment_log`, `retro_actions`, `decision_log`, `human_approvals`,
  `transcript`, `blocking_interactions`, ...) are append-only lists - a real per-key merge strategy for
  the optimistic-concurrency story is more tractable than it might sound at first, precisely because so
  much of the state shape is "append, never edit in place". `sprint_backlog`/`product_backlog` (items
  mutated in place - `stages_completed`, etc.) are the harder case and deserve explicit design
  attention in that story, not an afterthought.

## Roadmap
- Not yet targeted at a specific version - like EP-0007, this epic's own backlog should be prioritized
  against `specs/ROADMAP.md` once filed as real stories. The first two stories (optimistic-concurrency
  writes, session locking) are the natural starting point: independently valuable (they harden the
  existing single-session-at-a-time reality against a stale lock or an interrupted-and-resumed session
  today), and the hard prerequisite for everything else in this epic.
