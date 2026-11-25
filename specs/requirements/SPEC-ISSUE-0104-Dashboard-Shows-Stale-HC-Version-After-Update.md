# Issue

- Issue ID: SPEC-ISSUE-0104
- Title: Dashboard's HC Version column keeps showing the old version after updating Horseless Carriage
- Status: Done
- Priority: Should
- Owner: Till Krempel
- Last Updated: 2026-11-25

## Overview

GH issue #569: "After updating the Horseless carriage version in the dashboard does not update.
Instead it shows an old version. What could be causing this?"

The dashboard's **HC Version** column never read the installed Horseless Carriage version. It
showed a value the *agent container* wrote into the target repo's `.hc/state.json`. Two separate
mechanisms kept that value stale after an update (dashboard "Update" link -> `update_hc.run_update()`,
or `python3 update_hc.py`):

1. **The agent container reads the version baked into its image, not the checkout's current one.**
   - `dashboard.py:372` (pre-fix) rendered `state_data.get("hc_version") or "unknown"`. That value
     comes from `.hc/state.json`.
   - `agents/scrum_team/tools/scrum.py:340` sets `s["hc_version"] = _hc_version()` on every session
     init. `agents/scrum_team/tools/base.py:15-25`'s `_hc_version()` reads `_project_root() / "VERSION"`.
     Inside the container that is `/app/VERSION`.
   - `/app/VERSION` is the **build-time copy**: `agent.Dockerfile:99` `COPY VERSION .`. The
     `agent` service bind-mounts `./agents`, `./spec-templates`, `./eval` and the workbench templates
     read-only (`docker-compose.yaml:112-114`), but not `VERSION`. So the agent runs current source
     code while reporting the version from the last image build.
   - `update_hc.py:44` `_REBUILD_TRIGGER_FILES` deliberately rebuilds images only when
     Dockerfiles, requirements, compose files or entrypoints change (`update_hc.py:257`). A normal
     release bump changes `VERSION` and Python source, which doesn't trigger a rebuild. The
     baked-in `/app/VERSION` therefore stays at the version of whichever update last *did* rebuild.
     Every new agent session then wrote that old version back into `state.json`, so starting a new
     session didn't fix it either.
2. **Even with a correct container, `state.json` lags the update.** `hc_version` is only rewritten
   when a live agent session runs `init_scrum_state` and persists state. Between "Update finished"
   and the next agent session saving state (which may be a long time later for a resting project),
   the dashboard showed the previous version.

The display made it more confusing: the same cell appended `hc_commit` (`dashboard.py:378` pre-fix,
`git rev-parse --short HEAD` read from the host checkout on every render). After an update, the
cell showed the *new* commit next to the *old* version, e.g. `0.2.0 (abc1234)`, which looked like a
version string that was simply wrong.

Not a cause: the dashboard has no version cache. `project_snapshot()` re-reads `state.json` on every
10s auto-refresh, and only GitHub release lookups for the *Product* Version column are cached
(`_cached_latest_release`).

## Acceptance Criteria

- The HC Version column shows the installed checkout's `VERSION` (`<hc_path>/VERSION`), read fresh
  on every render. An update shows up on the next page refresh without restarting the dashboard.
- When `state.json`'s recorded `hc_version` differs from the installed version, the cell flags it
  (`state last saved by vX`) instead of hiding it. No flag when they match.
- The agent container reads the checkout's current `VERSION`. All three compose files bind-mount
  `./VERSION:/app/VERSION:ro` into the `agent` service, so new state saves record the correct version
  without needing an image rebuild.
- Tests that fail before the fix and pass after:
  - `tests/test_dashboard.py::TestProjectSnapshot::test_hc_version_reflects_the_installed_checkout_not_a_stale_state_json`
  - `tests/test_dashboard.py::TestRenderPage::test_flags_when_state_json_was_last_saved_by_a_different_hc_version`
  - `tests/test_docker_compose_env.py::TestAgentSeesTheCheckoutsCurrentVersion::test_version_file_is_bind_mounted_into_the_agent_container`

## Notes

- GH issue #569.
- **Migration for existing installations:** this change edits `docker-compose*.yaml`, which is in
  `update_hc.py`'s `_REBUILD_TRIGGER_FILES`. The update that brings in this fix therefore rebuilds
  and recreates the `agent` container with the new mount. No manual step is needed. A checkout
  updated by hand (`git pull`) needs a stack restart (`docker compose up -d` recreates the container
  because its config changed) before the mount takes effect.
- `VERSION` is still `COPY`'d into the image (`agent.Dockerfile:99`), so a production-style run
  without dev bind mounts still reports the version it was built from. The mount only overrides it
  the same way `./agents` already overrides the baked-in source.
- `RELEASE.md` "Tracking the HC version in the state repo" still holds. `state.json`'s `hc_version`
  is "the version that produced this data", and the dashboard now treats it that way instead of
  presenting it as the installed version.
- Out of scope: the agent *image* can still be older than the checkout for reasons
  `_REBUILD_TRIGGER_FILES` doesn't cover. That is a broader "rebuild needed" detector (e.g. an
  image label with the build commit compared against the checkout). Not needed for the version
  display once `VERSION` is mounted.

## Test Approach

- Host-only unit tests (`tests/`, no Docker): build a fake `hc_path` with `VERSION` `0.3.0` and a
  `state.json` recording `0.2.0`. Assert the snapshot reports `0.3.0`, keeps `0.2.0` as
  `hc_recorded_version`, and picks up a rewritten `VERSION` on the next snapshot with no caching.
  The rendered page shows the mismatch flag only when the two versions differ.
- Compose-file test: parse each of the three compose files with `yaml.safe_load` and assert that
  the `agent` service's `volumes` contain `./VERSION:/app/VERSION:ro`.

## Resolution

Fixed on branch `fix-569-dashboard-stale-version`:
- `dashboard.py`: the new `_installed_hc_version(hc_path)` reads the HC checkout's `VERSION` on
  every render. `project_snapshot()` uses it for `hc_version` (falling back to the recorded value,
  then `"unknown"`), adds `hc_recorded_version`, and `_render_page()` flags a mismatch.
- `docker-compose.yaml`, `docker-compose.local.yaml`, `docker-compose.local-hostollama.yaml`: the
  `agent` service bind-mounts `./VERSION:/app/VERSION:ro`.
