# Issue

- Issue ID: ISSUE-0038
- Title: Provider Picker Has No Remembered Default
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #87): "All other options have the same default as before, only the llm
provider has no default. Fix it and add the last selected config as default."

**Root cause**: `setup_llm.py`'s `main()` top-level provider-picker prompt
(`input("Choice [1-4]: ")`) had no default at all - worse than "always resets to option 1", a bare
Enter (which works for every other prompt in this same wizard) fell through to `die(f"Invalid
choice: {choice}")`. Every other prompt in the wizard already prefills from existing config on a
re-run: `current_interaction_level_choice()` reads `INTERACTION_LEVEL` from `.env`, the model-choice
prompts prefill from the active `litellm.yaml`/template, the GPU prompt defaults from detected
hardware + prior config. The provider picker was the one prompt with no such treatment.

The repo already has exactly the detection needed to fix this: `lib_llm_test.llm_active_config_path()`
+ `llm_active_provider()` (added for GH issue #36) determine which provider is currently configured by
checking whichever of `litellm.yaml` (cloud) or `config/model-templates/litellm.local-ollama.yaml`
(local) was written most recently, then reading that file's `model: <provider>/...` line - already
used by `doctor.py`/`lib_docker.py` for exactly this purpose.

## Acceptance Criteria
- Re-running `setup_llm.py` defaults the provider prompt to whichever provider is currently active
  (as `llm_active_provider`/`llm_active_config_path` already determine it), not always requiring a
  fresh explicit choice.
- A bare Enter accepts that default, matching every other prompt in the wizard's
  `input(...) or default_choice` idiom - it no longer falls through to `die()`.
- An invalid, non-blank choice (e.g. "9") still errors via `die()`, unchanged.
- No new state/marker file introduced - the "last selected provider" is derived from the same
  already-written artifacts every other part of the codebase already uses for this.

## Notes
- Mirrors `current_interaction_level_choice()`'s exact pattern (added for a previous "wizard should
  remember what's already configured" fix) - same numbered-choice-dict-and-fallback shape, just
  backed by `llm_active_provider()` instead of an env var read.
- On a completely fresh checkout (no prior `setup_llm.py` run), the shipped `litellm.yaml` already
  defaults to `gemini/gemini-1.5-pro`, so `current_provider_choice()` naturally resolves to "1"
  (Gemini) - identical to today's hardcoded behavior for a first-time user; the change only matters
  once a real choice has actually been made.

## Test Approach
- `tests/test_setup_llm.py::TestCurrentProviderChoice` (new) - no config files (defaults to Gemini),
  reads back anthropic/openai/local from the relevant config file, and an unrecognized/empty config
  falls back to Gemini.
- `pytest tests/`: 302 passed, no regressions.

## Resolution
- `setup_llm.py`: new `current_provider_choice(repo_root)` (mirrors `current_interaction_level_choice`),
  backed by `lib_llm_test.llm_active_provider(lib_llm_test.llm_active_config_path(repo_root))`. `main()`
  now computes this before printing the menu, marks the current choice `"(current)"` in the printed
  list, and accepts a bare Enter via `input(f"Choice [{default_choice}]: ").strip() or default_choice`.
