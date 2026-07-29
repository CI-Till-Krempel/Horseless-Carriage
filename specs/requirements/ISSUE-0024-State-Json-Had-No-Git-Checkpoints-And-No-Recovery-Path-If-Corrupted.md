# Issue

- Issue ID: ISSUE-0024
- Title: state.json Had No Git Checkpoints and No Recovery Path if Corrupted
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #59): "The sprint state must always stay restorable. By updating the state
json and committing to the repository, specific working states can function as checkpoints to
improve system stability. These checkpoints in the git are the fallback option if the state gets
corrupted. Use this mechanism to cleanly shutdown the state repo at the current state when the
container is stopped... Improve or simplify the approach to ensure that the state is always
restorable even if the container gets killed (not stopped)."

Investigation found `.hc/state.json` (the state repository's persisted mirror of the live session
state - see [State Repository](../../docs/STATE-REPOSITORY.md)) was written by `save_state_to_repo()`
(`agents/scrum_team/tools/scrum.py`) via a plain `write_text()`, with no git commit anywhere in the
codebase tied to that file - `STATE_REPO_PATH` being a git repository was incidental (used for
`git_push()`'s feature-branch/spec-doc pushes), never leveraged for state.json itself. Two concrete
risks followed: (1) a process killed mid-write could leave a torn/corrupted `state.json` behind with
no atomic-write protection, and (2) even a cleanly-written but application-level-wrong state had no
git history to fall back to - `check_state_repo.py`'s own validation already anticipated this
("Your state might be corrupted... you might need to manually fix it... or initialize a new one"),
but nothing actually gave it a corrected version to recover to.

`STATE_REPO_PATH` and `./sessions` are host bind mounts (`docker-compose.yaml`), not named Docker
volumes - they already survive `docker kill`/`stop`/`restart` unconditionally (only `docker compose
down -v` or deleting the host directory would lose them). So "restorable even if killed" isn't really
about surviving container destruction (the bind mount already guarantees that) - it's about surviving
a *torn write* or a *bad state* with no rollback path, which is exactly what git checkpointing fixes.

## Acceptance Criteria
- `save_state_to_repo()` writes `.hc/state.json` atomically (temp file + `os.replace`), so a process
  killed mid-write can't leave a half-written file behind.
- `save_state_to_repo()` also commits that snapshot to the state repository's local git history as a
  checkpoint, whenever `STATE_REPO_PATH` is actually a git repo - purely local, never pushed (pushing
  is `git_push()`'s own deliberate, protected-branch-aware job). A `STATE_REPO_PATH` that's a plain
  directory (not a git repo) behaves exactly as before - no crash, no git side effects.
- `load_state_from_repo()` falls back to the last git-committed checkpoint
  (`git show HEAD:.hc/state.json`) if the working-tree `state.json` is corrupted/unparseable, and
  repairs the working-tree file with the recovered content - closing the loop `check_state_repo.py`'s
  validation left open. Reports `recovered_from_git: true` when this path was taken.
- A corrupted `state.json` with no prior checkpoint available (never a git repo, or no commit touching
  that path yet) still reports a clear error rather than silently losing data or crashing.
- Since `save_state_to_repo()` is already called by nearly every state-changing tool (`log_decision`,
  budget spend, requirement sync, etc. - not just at shutdown), checkpointing on every save gives much
  stronger continuous protection against an ungraceful kill at any point than a shutdown-only hook
  ever could, without needing one at all.

## Notes
- **Not attempted in this change**: a container-shutdown (SIGTERM) hook that forces one last
  checkpoint, and a startup `git pull`/reset of the state repository before `init_scrum_state()` loads
  it ("the restore state should be updated once the specific config could be read"). Both are
  legitimate parts of the original issue, but implementing them means restructuring
  `entrypoint.sh`/`agents/scrum_team/scripts/run_agent.sh`'s process model - `entrypoint.sh` currently
  `exec`s directly into the final process (`adk web`/`adk run`), which becomes PID 1 and receives
  `SIGTERM` directly; catching that signal for a graceful shutdown hook means NOT `exec`ing (keeping a
  shell as PID 1, forwarding signals to a backgrounded child, waiting, then running cleanup) - a
  higher-blast-radius change to a script every container invocation depends on, which deserves its own
  focused change rather than being folded in alongside the two fixes above. Given that
  `save_state_to_repo()` now checkpoints continuously (on every save, not just at shutdown), the
  marginal safety a shutdown-only hook would add on top is small relative to the risk of a signal-
  forwarding bug affecting every container start/stop. Worth a follow-up issue of its own.
- Where the gap lived: `agents/scrum_team/tools/scrum.py`'s `save_state_to_repo`/`load_state_from_repo`
  - see `agents/scrum_team/tools/base.py`'s `_run()` (already used by `git_push()` for git identity/auth
  handling) for the git-command helper reused here for the local `add`/`commit`/`show` calls.

## Test Approach
- `agents/scrum_team/tests/test_state_persistence.py::TestStateCheckpointCommit` - a save against a
  real (locally git-init'd) state repo produces a checkpoint commit; a second no-op save doesn't
  error (git's own "nothing to commit" is swallowed); successive saves each add their own checkpoint;
  a plain (non-git) directory is left untouched by git.
- `agents/scrum_team/tests/test_state_persistence.py::TestLoadStateGitRecovery` - a corrupted
  `state.json` recovers the prior checkpoint's content and reports `recovered_from_git: true`;
  recovery repairs the working-tree file; a corrupted file with no prior checkpoint (or no git repo at
  all) reports a clear error instead of crashing.
- Pre-existing `TestStatePersistence` tests (14 tests, using a plain non-git directory) all pass
  unmodified - confirms the new checkpoint/recovery behavior is fully additive.
- Run via `docker compose --env-file .env.test run --rm --entrypoint "" -e PYTHONPATH=/app agent
  pytest agents/scrum_team/tests` (these tests need the container's `/app` paths - see
  `docs/TESTING.md`): 191 passed, no regressions across the full `agents/scrum_team/tests` suite.

## Resolution
- `agents/scrum_team/tools/scrum.py`:
  - `_write_state_atomically()`: temp file + `os.replace` instead of a plain `write_text`.
  - `_checkpoint_state_commit()`: best-effort local `git add` + `git commit` of `.hc/state.json` via
    `base._run()`, no-op if `repo_root` isn't a git repo.
  - `_parse_state_json()` / `_recover_state_json_from_git()`: shared JSON-validity check, and the
    `git show HEAD:.hc/state.json` recovery fallback.
  - `save_state_to_repo()` now writes atomically then checkpoints; `load_state_from_repo()` now falls
    back to git recovery (and repairs the file) when the working-tree copy is invalid.
- `docs/STATE-REPOSITORY.md`: added a "Checkpointing and recovery" section describing the above.
