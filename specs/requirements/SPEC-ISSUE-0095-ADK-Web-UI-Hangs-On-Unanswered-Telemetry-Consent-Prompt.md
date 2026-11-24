# Issue

- Issue ID: SPEC-ISSUE-0095
- Title: ADK web UI never starts - `adk web` blocks on an unanswered telemetry consent prompt
- Status: Done
- Priority: Must
- Owner: Till Krempel
- Last Updated: 2026-11-24

## Overview

After Dependabot bumped `google-adk` from `2.4.0` to `2.15.0` (PR #549, commit `8a35f2e`), every
installation's agent container came up `Up` but the ADK web UI was unreachable on its published
port (net-proxy's `${AGENT_WEB_HOST_PORT}` -> `agent:8000`, SPEC-ISSUE-0063). The agent log stops
right after `run_agent.sh`'s own banner:

```
--- Starting ADK web frontend on port 8000 ---
--- Most recent session: app=scrum_team id=... ---
Help improve the ADK (CLI and Web UI) by allowing Google to collect pseudonymized usage data?
...
This is OFF by default. You can opt out at any time using the 'adk telemetry disable' command or Web UI user settings.
```

Inside the container the `adk web --host 0.0.0.0 --port 8000 ...` process is alive, but nothing
is listening on 8000.

**Confirmed mechanism:** google-adk 2.15.0's top-level click group
(`google/adk/cli/cli_tools_click.py`, `main()`, ~line 410) prompts with
`input("Enable telemetry? [Y/n]: ")` before dispatching *any* subcommand other than `telemetry`
or `--help`, when both:

1. `sys.stdin.isatty()` is true - the `agent` service in `docker-compose.yaml` sets
   `stdin_open: true` / `tty: true` (required for `AGENT_MODE=cli`), so it always is; and
2. `read_telemetry_consent()` (`google/adk/utils/_telemetry_config.py`) returns `None` - no
   `"telemetry"` key in `~/.adk/config.json`, which never exists in a freshly created container.

In web/daemon mode nobody is attached to that TTY, so `input()` blocks forever and uvicorn never
starts. The same prompt would also block `adk run` (CLI mode - where a user could at least see and
answer it) and any `adk eval` run under a TTY. Note the prompt's default is **yes** - an
absent-minded Enter opts the installation into telemetry.

Reproduced against the built `agent` image with a pseudo-terminal (`pty.fork()` around
`adk web --port 8123 agents`): with no prior consent the output ends at `Enable telemetry?`; after
`adk telemetry disable` the same command reaches `Uvicorn running`.

## Acceptance Criteria

- The agent image records an explicit telemetry opt-out at build time
  (`RUN adk telemetry disable` in `agent.Dockerfile`'s `base` stage, after `pip install`), so no
  `adk` subcommand in any stage (`final`, `test`) ever prompts.
- `tests/test_agent_dockerfile.py::test_agent_dockerfile_disables_adk_telemetry_prompt` asserts
  the opt-out is present in the base stage and ordered after the ADK install - fails on `main`
  before this fix, passes after.

## Notes

- Regression introduced by the Dependabot bump #549 (`requirements.txt`'s `google-adk==2.15.0`);
  Dependabot PRs only run the mocked test suites, which never start `adk web` under a TTY, so
  nothing caught it before merge.
- Why build time rather than `run_agent.sh`: one place covers `adk web`, `adk run` and `adk eval`
  alike, adds no per-start latency, and the image runs as root with no volume mounted over
  `/root`, so `/root/.adk/config.json` baked into the image is what every container sees.
  Dropping `tty: true` instead was rejected - CLI mode needs it.
- Opt-out (not opt-in) is deliberate: Horseless Carriage should not silently enable third-party
  usage telemetry on a user's behalf; anyone who wants it can run `adk telemetry enable` or use
  the web UI's own settings.

## Test Approach

- Static Dockerfile assertion in the host-script suite (no Docker needed), matching the existing
  GH #95 locale-pinning test in the same file.
- Manual verification: PTY-driven `adk web` in a throwaway container from the built image, with
  and without the opt-out (see Overview).
