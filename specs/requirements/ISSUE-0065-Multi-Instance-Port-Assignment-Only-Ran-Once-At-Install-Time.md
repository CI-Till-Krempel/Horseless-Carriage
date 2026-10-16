# Issue

- Issue ID: ISSUE-0065
- Title: Multi-Instance Port Assignment Only Ran Once, At Install Time
- Status: Done
- Priority: Should
- Owner: Architect
- Last Updated: 2026-10-15

## Overview
GH issue #288 gave `new_project.py` one-time collision avoidance for `LITELLM_HOST_PORT`/
`AGENT_WEB_HOST_PORT`: at install, if the defaults (4000/8000) are already bound, it picks a
free port instead. That check only ever ran there. If two projects are installed back-to-back
while neither's stack is running yet, both can still end up configured with the *same* port -
the collision only surfaces later, as an opaque Docker bind failure, once both are actually
started. Neither `run.py` (direct `python3 run.py`) nor `dashboard.py`'s "Start" action
re-validates or reassigns a port before bringing a stack up, and `dashboard.py` doesn't show a
project's assigned ports at all - with several installations registered, there's no way to
tell which port belongs to which from the dashboard.

## Acceptance Criteria
- A new `lib_docker.ensure_dev_ports_available(repo_root, compose_args)` re-checks
  `LITELLM_HOST_PORT`/`AGENT_WEB_HOST_PORT` immediately before a stack actually starts (not
  just at install): no-op if this project's own stack is already running (its own port isn't
  "taken by someone else"); otherwise reassigns + persists to `.env` whatever is genuinely
  occupied by something else, via the same `is_port_free`/`find_free_port` primitives
  `new_project.py` already used (now shared, not install-script-private).
- `run.py` calls this right before `docker compose up`/`run`, printing a notice per reassigned
  port. It already always passes `--build` regardless, so no separate conditional rebuild is
  needed there.
- `dashboard.py`'s "Start" action calls this first and only adds `--build` to its `up -d` when
  a port actually had to change (today it has no `--build` at all) - and reports the
  reassignment in its status message.
- The dashboard's project table gains a "Ports" column (read-only) showing each registered
  project's current `LITELLM_HOST_PORT`/`AGENT_WEB_HOST_PORT`.

## Notes
- `find_free_port` now returns `None` on exhaustion instead of calling `die()` internally (the
  old behavior was tied to `new_project.py`'s own interactive-setup error handling) - each
  caller decides how to react: `new_project.py` still dies; `run.py`/`dashboard.py` just skip
  reassignment and let the real `docker compose up` failure (if any) surface on its own.
- Scope decision (confirmed with the project owner): display-only ports in the dashboard (no
  manual edit UI) - automatic reassignment already resolves the conflict.
- Originally numbered ISSUE-0064 (authored in parallel, off a different base, with the unrelated
  test-suite platform-agnosticism work in PR #451 which had already claimed that number) -
  renumbered to ISSUE-0065 to avoid a collision in `specs/requirements/`.

## Test Approach
- `tests/test_lib_docker.py`: `is_port_free`/`find_free_port` (moved from `new_project.py`'s
  own private copies) and new `ensure_dev_ports_available` coverage - no-op when this project's
  own stack is running, no-op when genuinely free, reassigns + persists when occupied, leaves
  `.env` untouched when no free port is found.
- `tests/test_new_project.py`: existing port-assignment tests updated to patch
  `lib_docker.is_port_free`/`lib_docker.find_free_port`; new case for the `find_free_port`-
  returns-`None` → `die()` path.
- `tests/test_run.py`: new `TestMainPortReassignment` confirms the startup notice prints only
  when a port actually changed; every other `_main`-exercising test class's shared mock setup
  stubs `ensure_dev_ports_available` to `[]` so existing tests stay isolated from real
  socket/subprocess/`.env` I/O.
- `tests/test_dashboard.py`: `project_snapshot`/`_render_page` surface the configured ports;
  new `TestStartStack` confirms `--build` is added only when a port was reassigned, and that a
  `docker compose` failure is still reported correctly either way.
