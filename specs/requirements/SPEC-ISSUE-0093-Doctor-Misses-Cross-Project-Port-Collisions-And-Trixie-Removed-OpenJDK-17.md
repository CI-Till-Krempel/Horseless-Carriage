# Issue

- Issue ID: SPEC-ISSUE-0093
- Title: doctor.py misses cross-project host-port collisions and workbench scripts that need an OpenJDK the base image no longer ships
- Status: Done
- Priority: Should
- Owner: Till Krempel
- Last Updated: 2026-11-24

## Overview

Two real projects on one machine (`heinzelmann`, `decide-o-mat-KMP`) both failed `python3 run.py`.
The output led with port notes, which made it look like a port problem:

```
NOTE: port 4001 (LITELLM_HOST_PORT) is now in use by something else - reassigned to 4004.
NOTE: port 8004 (AGENT_WEB_HOST_PORT) is now in use by something else - reassigned to 8005.
...
 > [agent base 6/8] RUN chmod +x /tmp/workbench-setup.sh && /tmp/workbench-setup.sh:
1.169 E: Unable to locate package openjdk-17-jdk
```

The output points at two separate gaps:

1. **The build failure itself.** `agent.Dockerfile` is `FROM python:3.11-slim`, a floating tag that
   now resolves to Debian **trixie**. Trixie no longer has OpenJDK 17 (it ships `openjdk-21-*`). The
   Architect-authored `heinzelmann/.hc/workbench-setup.sh` runs `apt-get install -y openjdk-17-jdk`,
   so every image build fails. Horseless Carriage's own reference templates
   (`config/development-workbench-templates/kotlin-multiplatform/workbench-setup.sh:38`,
   `react-vite-firebase/workbench-setup.sh:12`) still installed `openjdk-17-*` too, so any project
   seeded from them would break the same way. `doctor.py` didn't catch any of this.
2. **Port collisions between registered projects are invisible until start time.** The only guards
   are `new_project.py`'s install-time check and `lib_docker.ensure_dev_ports_available` at startup
   (GH #288). Both ask only `lib_docker.is_port_free`, i.e. "is this port bound *right now*". A
   registered project whose stack is stopped looks like it uses no ports at all. Another project can
   therefore be configured with (or reassigned to) the same `LITELLM_HOST_PORT`/
   `AGENT_WEB_HOST_PORT`, and that only shows up as a bind failure or a surprise reassignment once
   both are started. Nothing checks the registry (`~/.horseless-carriage/projects.json`) as a whole.

## Acceptance Criteria

- `doctor.py` reads every registered project's `.env` (plus the checkout it runs in) and detects any
  host port claimed more than once. Unless `--dry-run`, it reassigns every claimant except one to a
  port that is free on the machine **and** unclaimed by any registered project. The project that
  keeps the port is the first one with a running dev stack, otherwise the first in registry order. A
  claimant whose own stack is running is never rewritten; it is reported as a warning instead.
  `--dry-run` (which the dashboard uses) only warns and makes no Docker calls.
- `ensure_dev_ports_available` and `new_project.py` also treat ports claimed by other registered
  projects as taken, so the collision isn't re-created on the next start or install.
- `doctor.py` raises an ERROR when `.hc/workbench-setup.sh` installs `openjdk-<N>` with `N < 21` and
  names the replacement package (e.g. `openjdk-17-jdk` -> `openjdk-21-jdk`). Commented-out lines are
  ignored.
- Reference workbench templates install `openjdk-21-*`.
- Host tests can never touch the developer's real registry: an autouse fixture in `tests/conftest.py`
  redirects `project_registry.REGISTRY_PATH` to a tmp location.

## Notes

- Port gap: `lib_docker.is_port_free`/`find_free_port`/`ensure_dev_ports_available`,
  `new_project._configure_for_target_project`. Fixed in `lib_docker.registered_dev_port_claims`,
  `find_registered_port_conflicts`, `repair_registered_port_conflicts`, and the `exclude` parameter
  on `find_free_port`.
- JDK gap: `doctor._unavailable_openjdk_package`, called next to the existing SPEC-ISSUE-0064 JVM
  workbench check.
- The affected product repo's own `.hc/workbench-setup.sh` still has to be fixed in that repo (it's
  Architect-owned, normally changed via `propose_framework_change`). `doctor.py` reports the exact
  replacement but never rewrites another repo's build script.

## Test Approach

- `tests/test_registered_port_conflicts.py`: claim discovery (defaults, stale entries,
  de-duplication), conflict detection, repair (keeper choice, a running keeper registered later,
  never picking a bound or claimed port, running claimants left alone), no Docker calls in dry run
  or when there are no conflicts, `ensure_dev_ports_available` avoiding stopped projects' claims,
  `doctor.check` end-to-end for repair and dry-run, and the OpenJDK check (17 -> error with
  suggestion, 21 fine, comments ignored).

## Resolution

Implemented as described above. Verified manually: a dry run of `repair_registered_port_conflicts`
against the real three-project registry found no current collisions. With heinzelmann's script
switched to `openjdk-21-jdk-headless`, its agent image builds again.
