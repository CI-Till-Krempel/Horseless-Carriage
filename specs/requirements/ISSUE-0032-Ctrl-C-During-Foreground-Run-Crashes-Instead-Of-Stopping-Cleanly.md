# Issue

- Issue ID: ISSUE-0032
- Title: Ctrl+C During Foreground Run Crashes Instead Of Stopping Cleanly
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-07-29

## Overview
Reported (GitHub issue #74): after starting the stack with `setup_all.py` (which hands off to
`run.py`), stopping the foreground process with Ctrl+C on the command line produced a crash-looking
traceback instead of a clean stop, on a real Windows run.

**Root cause**: `run.py`'s foreground code paths (`cli` mode's `docker compose run`, and web/daemon
mode's `docker compose up`) call `subprocess.run(cmd, env=proc_env)` and block on it directly from
`main()`. On at least one real Windows run, Ctrl+C raised `KeyboardInterrupt` from inside
`subprocess.run`'s own wait (`subprocess.communicate()` -> `_winapi.WaitForSingleObject`), and nothing
in `run.py` caught it - it propagated all the way out of `main()` as a raw, uncaught exception and
printed a full Python traceback. This directly contradicts the tool's own UX: both foreground code
paths print "Press Ctrl+C to stop"/"Press Ctrl+C to exit" immediately beforehand, describing Ctrl+C as
the normal, expected way to end the run - not something that should look like a bug.

## Acceptance Criteria
- Pressing Ctrl+C during any foreground `run.py` invocation (`web`, `cli`, or `dev` variants of
  either) exits with a clean "Stopped." message and exit code 0, not a raw traceback.
- No change to non-interrupted behavior: normal exits (including non-zero `docker compose`/`docker
  compose run` return codes) still propagate their original exit code unchanged.
- The fix is at the `run.main()` level so it covers every foreground `subprocess.run` call in this
  module (web-mode `up`, daemon-mode `up -d`, cli-mode `run`) without duplicating a
  try/except at each call site.

## Notes
- `setup_all.py`'s `offer_to_start()` calls `run.main()` directly (in-process, not as a subprocess), so
  this fix also covers Ctrl+C during a run started that way - the reported path.
- Daemon mode's `subprocess.run(["docker", "compose", ..., "up", "-d", ...])` returns immediately
  (detached), so Ctrl+C during the brief foreground `docker compose up -d` call is the same interrupt
  window as the other two paths; nothing extra was needed for daemon mode specifically.

## Test Approach
- `tests/test_run.py::TestMainKeyboardInterrupt` - a `subprocess.run` mock that raises
  `KeyboardInterrupt` is exercised across all three mode branches (default/web, `cli`, `daemon`);
  asserts `run.main()` exits with code 0 and prints "Stopped." rather than letting the exception
  propagate.
- `pytest tests/test_run.py`: 22 passed (plus one pre-existing, unrelated `TestWaitForHttp` failure -
  a sandboxed-environment socket-bind permission error, reproducible on `main` before this change and
  unrelated to it).
- `pytest tests/`: 281 passed, no regressions (same pre-existing socket-bind errors in
  `test_doctor.py`/`test_lib_llm_test.py`, unrelated to this change).

## Resolution
- `run.py`: split `main()` into a thin wrapper that calls the original body (now `_main()`) inside a
  `try/except KeyboardInterrupt`, printing "Stopped." and exiting 0 on interrupt. `_main()` is
  otherwise byte-for-byte the previous `main()` body.
