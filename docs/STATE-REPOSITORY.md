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

The `check_state_repo.py` script verifies that your state repository is in the expected state for the tools to work correctly. It checks for the correct directory structure and ensures no stray template files are present in the `specs` directory.

```bash
python3 check_state_repo.py
```
