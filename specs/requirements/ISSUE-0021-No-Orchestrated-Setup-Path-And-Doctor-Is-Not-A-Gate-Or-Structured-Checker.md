# Issue

- Issue ID: ISSUE-0021
- Title: No Orchestrated Setup Path, and doctor.py Is Neither a Gate Nor a Structured Checker
- Status: Done
- Priority: Should
- Owner: Scrum Team
- Last Updated: 2026-07-29

## Overview
Requested: orchestrate the whole setup process into one script, guiding the user through every step
to a working setup, using `doctor.py` as a gatekeeper before starting, improving `doctor.py` to keep
a list of actionable items, and adding a developer mode (rebuild images every run, more verbose
logging).

Prior state:
- No single script walked a user through `setup_llm.py` -> `setup_project.py` -> validation -> actually
  starting the agent; README's own "Quick start" listed all four commands, but running them in order
  was left entirely to the user.
- `doctor.py`'s `run()` returned early (`return 1`) at the first problem found (missing Docker, `.env`,
  `LITELLM_MASTER_KEY`, or `STATE_REPO_PATH`), so a completely unconfigured repo only ever reported
  ONE problem per invocation - fix it, rerun, discover the next one, repeat. There was also no
  structured way for another script to ask "what's wrong" - only printed text and a 0/1 exit code.
- `run.py` re-implemented a subset of `doctor.py`'s own checks inline (Docker present, `.env` exists,
  `STATE_REPO_PATH` set and valid) rather than actually using `doctor.py`, so the two could drift.
- Neither `run.py` nor any other script offered a way to force a fresh image rebuild + verbose
  logging together as a single "developer mode" toggle (`rebuild_images.py`, added separately, only
  covered the rebuild half).

## Acceptance Criteria
- A new orchestrator script runs `setup_llm.py`, then `setup_project.py`, then gates on `doctor.py`
  (looping fix-and-retry until there are no more ERROR-level items), then offers to start the agent
  via `run.py` - while every individual script remains fully usable standalone.
- Not literally named `setup.py`: this repo already deliberately avoided that name for
  `setup_project.py` (`pip install .`/legacy setuptools tooling would try to execute a root-level
  `setup.py` as a build script) - the same reasoning applies here, so the orchestrator is
  `setup_all.py` instead.
- `doctor.py` collects every problem into a structured punch list (`ActionableItem`/`DoctorResult`)
  instead of stopping at the first one - a fully broken setup reports every blocking problem in one
  pass. `check()` returns the structured result for programmatic use (`run.py`'s gate, `setup_all.py`);
  `run()` stays a thin, backward-compatible `int`-returning wrapper around it so existing callers/tests
  are unaffected.
- `run.py` uses `doctor.check()` (not its own duplicated inline checks) as a gate: refuses to start the
  stack at all while any ERROR-level item remains, printing the full punch list.
- `python3 run.py dev` (or accepting `setup_all.py`'s developer-mode prompt) rebuilds the `agent`
  (and `ollama`, if active) images fresh before starting (reusing `rebuild_images.py`'s logic) and
  runs with `LOG_LEVEL=debug` for that invocation only, without needing that persisted to `.env`.
- Tests exist for the doctor punch-list behavior, the `run.py` gate/dev-mode additions, and
  `setup_all.py`'s orchestration logic.

## Notes
- Where the gaps lived: no orchestrator script existed at all; `doctor.py`'s `run()` (early returns);
  `run.py`'s `main()` (duplicated ad-hoc checks instead of calling `doctor.py`, no rebuild/dev-mode
  path).
- `compose_file_args` (previously defined in `run.py`) moved to `lib_docker.py`: `rebuild_images.py`
  needs it for developer mode, and `rebuild_images.py` importing `run` while `run.py` imports
  `rebuild_images` would be a circular import - `lib_docker.py` is a leaf module neither depends on,
  so both can import it directly. `run.compose_file_args` is kept as a thin re-export so existing
  callers/tests are unaffected.
- `rebuild_images.py` gained a `rebuild(compose_args, no_cache=False)` function extracted from its
  `main()` - the shared logic `run.py`'s developer mode calls directly with an already-known
  `compose_args`, versus `main()`'s own CLI path which still computes it via
  `lib_docker.compose_file_args` (using a local, in-function `import run` there specifically, to
  avoid `rebuild_images.py`'s own module level ever needing `run` - `run.py` importing
  `rebuild_images` at module level must not round-trip back into a partially-initialized `run`
  module).
- `doctor.check()` gained a `skip_llm_probe` parameter: `run.py`'s pre-flight gate runs before any
  container is started, so a live "is the proxy already reachable" check there could only ever
  report "not reachable" while costing several real seconds for nothing - the active
  provider/key-configuration checks (cheap, local) still run either way.

## Test Approach
- `tests/test_doctor.py`: existing guard-clause tests updated (no more early returns, so they now
  also reach the LLM-configuration section - added `_patch_proxy_unreachable` where missing to avoid
  a real network wait); new `TestCheckStructuredResult` (severity classification, `.ok`/`.has_errors`,
  `.errors()`/`.warnings()`, `print_summary()`, `run()`/`check()` staying in lockstep) and a
  `test_multiple_errors_are_all_collected_not_just_the_first` regression test; new
  `test_skip_llm_probe_never_calls_wait_for_proxy`.
- `tests/test_run.py`: `TestParseArgs` updated for the new `dev` field; new
  `TestMainDoctorGatekeeper` (blocks starting `docker compose` when doctor reports errors, calls
  `check()` with `skip_llm_probe=True`) and `TestMainDeveloperMode` (dev mode rebuilds before
  starting and sets `LOG_LEVEL=debug`; a failed rebuild stops before `docker compose up`; non-dev
  mode does neither).
- `tests/test_rebuild_images.py`: `TestMain`'s mocks updated for `compose_file_args` now living in
  `lib_docker`; new `TestRebuild` unit-testing the extracted function directly.
- `tests/test_setup_all.py` (new): `confirm`, `run_step` (success, clean/bare `SystemExit`, nonzero
  `SystemExit`, `KeyboardInterrupt` propagation, unexpected-exception handling), `run_guided_step`
  (retry loop), `run_doctor_gate` (retry loop), `offer_to_start` (declines, defaults, all-options,
  `default_dev` honored), and `main()` (happy path, `--dev` flag propagation, stopping early on a
  failed guided step or an unresolved doctor gate).
- Full suite: 208 passed (7 pre-existing, unrelated sandbox errors from binding a local test HTTP
  server, reproducible on `main`).

## Resolution
- Added `setup_all.py`: guided orchestrator chaining `setup_llm.py` -> `setup_project.py` ->
  `doctor.py` gate -> offer to start via `run.py` (including developer mode). `--dev` pre-answers the
  final developer-mode question to yes.
- `doctor.py`: added `ActionableItem`/`DoctorResult` dataclasses; `check()` is the new
  comprehensive, non-early-returning implementation; `run()` is now a thin wrapper
  (`1 if check(...).has_errors else 0`) kept for backward compatibility; added `skip_llm_probe`.
- `run.py`: `main()` now gates on `doctor.check(..., skip_llm_probe=True)` instead of its own inline
  checks; added `dev` to `parse_args`; developer mode calls `rebuild_images.rebuild()` before
  starting and overrides `LOG_LEVEL=debug` for that invocation; `main()` now accepts an optional
  `argv` parameter so `setup_all.py` can hand off without mutating `sys.argv`.
- `lib_docker.py`: gained `compose_file_args` (moved from `run.py`, which now re-exports it).
- `rebuild_images.py`: extracted `rebuild(compose_args, no_cache=False)` from `main()`.
- Updated `README.md`, `docs/RUNNING.md`, `docs/SETUP.md`, `MANUAL.md` to document `setup_all.py`,
  the doctor gate/punch list, and developer mode.
