# Issue

- Issue ID: SPEC-ISSUE-0097
- Title: `new_project.py --target-repo` runs the guided setup against the launching checkout, not the added project
- Status: Done
- Priority: Must
- Owner: Till Krempel
- Last Updated: 2026-11-25

## Overview

`python3 new_project.py --target-repo /path/to/product`, run from an existing Horseless Carriage
checkout, adds HC as `product/horseless-carriage/` and pre-fills *that* submodule's `.env`
(`_install_if_nested` -> `_configure_for_target_project(repo_root, ...)`, which works on explicit
`repo_root` paths and is correct). It then hands off to the guided `setup_all.py` flow. That flow
wrote the provider, model, API key, budget and state-repo answers into the **launching
checkout's** `.env`, `litellm.yaml` and `config/model-templates/litellm.*.yaml`, not the new
submodule's. The project that was just added stayed half-configured, and the checkout the user
launched from (often their own working project's installation) had its config silently overwritten.
Reported as GH issue #562: "when running add_project only the config of the added project should
be affected (apart of course of the project registry)."

**Confirmed mechanism** (pre-fix `main`, commit `85d12b4`):

- `new_project.py:255`: `this_checkout = Path(__file__).resolve().parent`. Every module
  `new_project.py` imports (`setup_all`, and through it `setup_llm`, `setup_project`,
  `check_state_repo`, `doctor`, `run`; `new_project.py:77`, `setup_all.py:55-60`) is loaded from
  this launching checkout, so each module's `__file__` points there.
- `new_project.py:265-266`: `os.chdir(repo_root); setup_all.main()`. This switches the cwd to the
  new submodule, then calls the **launching checkout's** `setup_all.main()` in-process.
- `setup_all.py:186` -> `setup_llm.main()`. Its first statement, `setup_llm.py:1204`, is
  `os.chdir(Path(__file__).resolve().parent)`, which moves the cwd back to the launching checkout
  before the first prompt. Everything after that uses cwd-relative paths:
  `env_path = Path(".env")` (`setup_llm.py:955`, `:1042`), `Path("litellm.yaml")` (`:1016`),
  `Path("config/model-templates/litellm.local-ollama.yaml")` (`:1162`).
- The later steps also anchor themselves to the launching checkout: `setup_project.py:27` (same
  `os.chdir(Path(__file__)...)`, then `.env`/`project_registry.sync_from_env(Path(".").resolve())`
  at `:62`/`:86`); `check_state_repo.py:357` (`run(Path(__file__).resolve().parent)`);
  `setup_all.py:184` (`repo_root = Path(__file__).resolve().parent`, used for the doctor gate);
  and `run.py:339` (`run.main`, reached via `setup_all.py:162`).

So the `os.chdir(repo_root)` in `new_project.py` had no lasting effect. Every step's working
directory comes from its own module's `__file__`, and the only copy of those modules in the process
was the launching checkout's. The no-`--target-repo` path (running `new_project.py` from inside an
existing submodule) was never affected, because there `this_checkout == repo_root`.

A side effect: `setup_project.py:86` re-registered the **launching** checkout in
`~/.horseless-carriage/projects.json`, which made the registry entry the user expected for the new
project look like it belonged to the wrong installation.

## Acceptance Criteria

- `new_project.py --target-repo X` runs the guided setup with every config write landing in
  `X/horseless-carriage/` (`.env`, `litellm.yaml`, `config/model-templates/`). Apart from the
  machine-wide project registry, nothing in the launching checkout is written.
- A failing setup in the submodule makes `new_project.py` exit with that same non-zero code.
- Running `new_project.py` with no `--target-repo` from inside a submodule (or a standalone checkout)
  behaves exactly as before: in-process `setup_all.main()` for that same checkout.
- A test that fails against the pre-fix `os.chdir(repo_root); setup_all.main()` hand-off and passes
  after.

## Resolution

`new_project.py` gained `_run_setup_all(this_checkout, repo_root)`. When `repo_root` is a different
checkout from the one `new_project.py` was loaded from (the `--target-repo` case), it runs that
checkout's **own** `setup_all.py` as a child process
(`subprocess.run([sys.executable, repo_root / "setup_all.py"], cwd=repo_root)`) and passes on a
non-zero exit code. Every `__file__`-relative `os.chdir` in the chained steps then resolves inside
the added project. As a bonus, the setup that runs is the version that was just checked out into the
submodule, which is also the version that project will run with. When `repo_root == this_checkout`,
the old in-process path is kept.

The alternative was to thread an explicit `repo_root` through `setup_all`/`setup_llm`/
`setup_project`/`check_state_repo`/`run`, replacing each one's `os.chdir(Path(__file__)...)`. That
would touch five scripts' standalone entry points and their tests for no behavioral gain over
running the submodule's own copy, so it was not done.

## Notes

- GH issue #562 ("add project script runs setup in the wrong directory").
- Origin of the hand-off: GH issue #288 (`new_project.py`), registry: GH issue #310.
- No guardrail or budget mechanism is touched, and nothing under `agents/scrum_team/` changes.

## Test Approach

- `tests/test_new_project.py::TestMainHandsOffSetupToTheAddedProject`:
  - `test_target_repo_setup_runs_in_the_added_submodule_not_this_checkout`: a fake submodule whose
    `setup_all.py` writes `.env` into its cwd and records the cwd. Asserts the launching checkout's
    in-process `setup_all.main` is never called, and that the submodule's `.env`/cwd are the ones
    written. Fails on pre-fix `main` (the in-process mock is called and the submodule's
    `setup_all.py` never runs).
  - `test_failing_submodule_setup_propagates_its_exit_code`: submodule setup exits 3, so
    `new_project.main()` raises `SystemExit(3)`. Also fails pre-fix.
  - `test_without_target_repo_setup_still_runs_in_process_for_this_checkout`: regression guard
    for the unchanged no-`--target-repo` path.
