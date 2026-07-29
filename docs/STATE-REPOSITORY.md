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
recovers the last git-committed checkpoint (`git show HEAD:.hc/state.json`) and repairs the
working-tree file with it, rather than failing outright - this is the fallback path the checkpoint
commits above exist to make possible. A state repository that was never a git repo to begin with (or
one with no checkpoint commits yet) has no such fallback available.

## State Repository Check

The `check_state_repo.py` script verifies that your state repository is in the expected state for the tools to work correctly. It checks for the correct directory structure and ensures no stray template files are present in the `specs` directory.

```bash
python3 check_state_repo.py
```
