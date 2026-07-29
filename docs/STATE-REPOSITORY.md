[← Back to README](../README.md)

# State Repository

The **State Repository** is the team's "Source of Truth." It is a dedicated directory (a Git repository) where the agents persist all project-related data.

`setup_llm.py` sets this up for you interactively (creating the directory, and either cloning your target repo into it or `git init`-ing a fresh one) - see [Setup § 1. Guided LLM/project setup](SETUP.md#1-guided-llmproject-setup-setup_llmpy). This page describes what it is and how to check its health once it exists.

## Concept

Unlike the session history (which is transient and internal), the State Repository contains human-readable artifacts and the official project state. This separation allows the agents to be ephemeral while the project remains permanent.

## Structure

- **`state.json`**: The internal machine-readable state of the Scrum artifacts (backlog, impediments, etc.).
- **`specs/`**: A directory containing the actual generated documents (PRDs, ADRs, Stories) based on the templates in `spec-templates/`. See [Architecture § Repository documentation structure](ARCHITECTURE.md#repository-documentation-structure) for what goes in here and the story-workflow rules that govern it.

## Usage

- **Configuration**: `STATE_REPO_PATH` in your `.env` points at your target repository. Set interactively by `setup_llm.py`, or by hand.
- **Persistence**: Tools used by the agents automatically commit changes to this repository (if configured) or write them directly to the filesystem.

## Checkpointing and recovery

Every `save_state_to_repo()` call (which most state-changing tools call automatically) writes
`.hc/state.json` atomically - via a temp file + rename, so a process killed mid-write can't leave a
torn/corrupted file behind - and then, if `STATE_REPO_PATH` is itself a git repository, commits that
snapshot locally as a checkpoint. This is purely local (never pushed - that's a separate, deliberate
step via the agent's own `git_push` tool); it exists so `.hc/state.json`'s git history is a trail of
restorable checkpoints, not just whatever bytes happen to be sitting in the working tree right now.

If `load_state_from_repo()` ever finds `.hc/state.json` corrupted or unparseable, it automatically
searches git history (newest first) for the most recent commit whose own snapshot still parses, and
repairs the working-tree file with it, rather than failing outright - this is the fallback path the
checkpoint commits above exist to make possible. It doesn't stop at just the latest commit (`HEAD`):
if the *latest* checkpoint was itself corrupted before anyone noticed, an earlier one may still be
recoverable. A state repository that was never a git repo to begin with (or one with no checkpoint
commits yet) has no such fallback available.

### If automatic recovery isn't possible either

If state.json is corrupted *and* no checkpoint anywhere in git history is usable either (GH issue
#85), the session starts with blank/default state rather than crashing - but you're not just stuck
with that. The Orchestrator will tell you this happened at the start of your very first message
(state_json_corrupted in the state passed to the model), and offers three tools to actually recover:

- **`get_corrupted_state_raw_content()` + `save_repaired_state(repaired_state)`** - "repair it with
  help of the LLM": read the raw corrupted file, ask the Orchestrator to reconstruct a valid state
  from it, then persist the result (validated against the real `ScrumState` schema before it's
  written, so a bad repair attempt can't make things worse).
- **`reset_state_from_git()`** - the same history-search `load_state_from_repo()` already does
  automatically, available as an explicit, on-demand tool.
- **`clear_corrupted_state()`** - deletes the corrupted file outright so the next session starts
  genuinely fresh, rather than the corruption silently lingering on disk.

All three refuse to run against a state.json that's currently perfectly fine - they only ever act on
a genuinely corrupted file, never as a way to reset good state.

## State Repository Check

The `check_state_repo.py` script verifies that your state repository is in the expected state for the tools to work correctly. It checks for the correct directory structure, ensures no stray template files are present in the `specs` directory, and (if `.hc/state.json` already exists) validates it.

```bash
python3 check_state_repo.py
```

`setup_all.py`'s guided flow runs this for you right after `setup_llm.py` creates/clones the repository. `doctor.py` also runs the cheap part of this check (the `specs/` directory + stray-template checks, not the heavier `state.json` validation) on every invocation, so a broken state repository shows up in its punch list too - run `check_state_repo.py` directly for the fuller picture doctor.py's summary points you to.

If validation fails and you're running it interactively (a real terminal), it offers the same
repair/reset/delete choice as the in-chat tools above, minus the LLM-assisted repair option (this
script runs before the agent/container even exist, so it has no LLM access) - reset to the last
usable checkpoint anywhere in git history, delete state.json and start fresh, or leave it as-is (and
let the Orchestrator attempt an LLM-assisted repair once you do start a session). Non-interactive
runs (CI, `doctor.py`) are unaffected - they still just report the failure.
