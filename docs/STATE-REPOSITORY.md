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

## State Repository Check

The `check_state_repo.py` script verifies that your state repository is in the expected state for the tools to work correctly. It checks for the correct directory structure, ensures no stray template files are present in the `specs` directory, and (if `.hc/state.json` already exists) validates it.

```bash
python3 check_state_repo.py
```

`setup_all.py`'s guided flow runs this for you right after `setup_llm.py` creates/clones the repository. `doctor.py` also runs the cheap part of this check (the `specs/` directory + stray-template checks, not the heavier `state.json` validation) on every invocation, so a broken state repository shows up in its punch list too - run `check_state_repo.py` directly for the fuller picture doctor.py's summary points you to.
