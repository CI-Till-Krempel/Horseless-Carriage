# Issue

- Issue ID: SPEC-ISSUE-0103
- Title: Dashboard Start/Stop/Update buttons discard their command output; no way to see the agent's logs from the dashboard
- Status: Done
- Priority: Should
- Owner: Till Krempel
- Last Updated: 2026-11-25

## Overview

GH issue #568: "The logs of all calls caused by the update buttons in the dashboard are lost.
Generally it would be great to be able to see the logs of the agent in the dashboard."

Every button on `dashboard.py`'s project table runs real commands (`docker compose up -d` /
`down`, `git fetch`, `git checkout`, `docker compose build --pull`), but none of their output was
ever kept anywhere a dashboard user could read it (line numbers are `origin/main` at `85d12b4`):

- `dashboard.py:431` (`_start_stack`) and `dashboard.py:464` (`_stop_stack`) run
  `subprocess.run(cmd, capture_output=True, ...)` and then only look at `result.stdout`/`stderr`
  on failure (`:435`, `:467`). On success the output is dropped and the user sees `Started.` /
  `Stopped.` (`:452`, `:467`).
- `update_hc.py:66` (`_git`) captures every git call the same way; `run_update` only surfaces
  `fetch`/`checkout` output when they fail (`:225`, `:238`). A successful update leaves no record
  of which commits were fetched or what `checkout` did.
- `rebuild_images.py:72` (`rebuild`) runs `subprocess.run(cmd)` with no capture at all, so the
  child inherits the dashboard server's own stdout fd: the whole `docker compose build` output goes
  to whatever terminal launched `python3 dashboard.py` (often nowhere visible). `update_hc.py:261`
  then tells the dashboard user `Image rebuild failed (exit N) - see output above.` There is no
  "above" in the browser.
- `update_hc.py:270` redirects `doctor.check()`'s report into a throwaway `io.StringIO()`.
- Whatever message survives only reaches the page as a one-shot `?msg=` query parameter on the 303
  redirect (`dashboard.py:692`). It is not persisted: once the user navigates away, or the
  dashboard restarts, it's gone.

The second half of the issue: the dashboard had no view of the agent container's own log at all.
The only way to see what the team was doing was a terminal `docker compose -p
horseless-carriage-dev logs agent` with the right `-f` files and project name, which is exactly
the scoping `lib_docker.compose_project_args` exists to get right (GH issue #169).

## Acceptance Criteria

- Every Start/Stop/Update click appends a block to a persistent per-project log file
  (`~/.horseless-carriage/dashboard-logs/<project>-<hash>.log`, next to `projects.json`; never
  inside the checkout, whose dirty-tree check `update_hc.run_update` relies on). The block holds
  a start marker (time, action, path), every command run (`$ cmd`, its stdout/stderr,
  `[exit code N]`), the message shown on the page, and a `RESULT` line: `OK` or `FAILED` with the
  non-zero exit codes, or `FAILED (unexpected error)` plus a traceback.
- `rebuild_images.rebuild(capture=True)` (what `run_update` now uses) routes the build output
  through `print()`, so the log records it. The CLI default (`capture=False`) keeps live TTY
  output.
- The log is bounded: once over 2 MB it's trimmed to the last ~1 MB before the next append.
- Credential-shaped strings (`sk-...`, `ghp_...`/`github_pat_...`, `AIza...`, `xox?-...`, PEM
  private keys, `*_API_KEY=`/`*TOKEN=`/`*SECRET=`/`*PASSWORD=`/`*MASTER_KEY=` assignments) are
  masked before anything reaches disk and again before rendering.
- A new read-only `/logs?hc_path=...` page (linked per row as **Logs**, and as **Full output of
  this action** right after a button redirect) shows the latest action's block with its overall
  status, earlier actions (last 400 lines, collapsed), and the last 200 lines of `docker compose
  <dev-scoped args> logs --no-color --timestamps --tail 200 agent`. Like the action routes, it
  only accepts a path that's in the project registry.
- Tests that fail on `origin/main` and pass after:
  `tests/test_dashboard.py::TestStartStack::test_successful_start_output_is_printed_not_dropped`,
  `TestStopStackLogsOutput`, `TestRunLoggedAction`, `TestAgentContainerLogs`, `TestLogsRoutes`,
  `TestRedactSecrets`, `TestActionLogPath`;
  `tests/test_update_hc.py::TestRunUpdate::test_fetch_and_checkout_output_is_printed_for_the_action_log`
  and `test_rebuild_is_requested_in_capture_mode`;
  `tests/test_rebuild_images.py::TestRebuild::test_capture_mode_echoes_build_output_and_exit_code_through_print`.

## Notes

- GH issue #568.
- Host-only change (`dashboard.py`, `update_hc.py`, `rebuild_images.py`). Nothing under
  `agents/scrum_team/` changes, so the ADK evalset is unaffected.
- Capture works by redirecting `sys.stdout`/`sys.stderr` for the duration of one action, tee'd to
  the log file (redacted) and the server's own console (unchanged). That is only safe because
  `dashboard.py` serves with a single-threaded `HTTPServer`. If it ever moves to
  `ThreadingHTTPServer`, `run_logged_action` needs an explicit per-action writer instead.
- Actions are still synchronous: the browser waits on the request until the update finishes, then
  the log is complete. Live streaming of an in-progress action is out of scope. The `/logs` page's
  15s auto-refresh plus the incremental (flush-per-write) log file means an action started in
  another tab is visible while it runs.
- Redaction is best-effort pattern matching, not a guarantee. The page says so, and is bound to
  `127.0.0.1` by default (GH issue #239).

## Test Approach

- Unit tests with mocked `subprocess.run` and a `project_registry.REGISTRY_PATH` patched into
  `tmp_path`, so no test writes under the real `~/.horseless-carriage/`.
- `update_hc` tests reuse the real local git origin/clone fixtures and assert the
  `$ git fetch ...` / `$ git checkout ...` / `[exit code 0]` lines reach stdout.
- The handler is driven through a socket-less `_DashboardHandler` subclass, covering registry
  gating (404, no `docker` call for an unregistered path), HTML escaping of log content, and the
  redirect carrying the log link.
- All fake credentials in tests are assembled at runtime (`"sk-" + "FAKE" * 6`), so the repo holds
  no literal token-shaped string.

## Resolution

Implemented in branch `fix-568-dashboard-action-logs`: `dashboard.run_logged_action`,
`_render_logs_page`, `_agent_container_logs`, `redact_secrets`, the `/logs` route,
`update_hc.print_command_result` / `_git_logged`, and `rebuild_images.rebuild(capture=...)`.
