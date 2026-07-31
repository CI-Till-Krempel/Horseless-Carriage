[← Back to README](../README.md)

# Running the Agent

Run the agent using the `run.py` script:

```bash
python3 run.py
```

This script will:
1. Run `doctor.py` as a gate - refuses to start at all while any ERROR-level item remains (missing
   `.env`, no state repository, etc.), printing the full punch list of everything that needs fixing.
2. Build and run the agent container.
3. Wait for the LiteLLM dashboard (and, in web mode, the ADK web UI) to come up, then open them in your default browser.

`run.py` supports four keywords, which can be combined:

| Command | Behavior |
|---|---|
| `python3 run.py` | **Default.** ADK web frontend, foreground, at `http://localhost:8000`. |
| `python3 run.py cli [query...]` | Interactive CLI session in your terminal instead of the web UI. |
| `python3 run.py daemon` | Add to either of the above to run detached (`python3 run.py daemon` or `python3 run.py cli daemon`). |
| `python3 run.py dev` | Add to any of the above for **developer mode**: rebuilds the `agent`/`ollama` images fresh before starting (see `rebuild_images.py`) and runs with verbose (`debug`) logging for that invocation, without needing that persisted to `.env`. |

The LiteLLM admin dashboard (`http://localhost:4000/ui`) is opened automatically in every mode.

## Running in Daemon Mode

To run the agent in the background:

```bash
python3 run.py daemon
```

To view logs when running in daemon mode:

```bash
docker compose logs -f agent
```

## Watch Mode: Get Notified of New Work

The team processes work single-threaded/turn-based: the `ScrumOrchestrator` delegates to one
sub-agent at a time via ADK's `transfer_to_agent`, and tools mutate `tool_context.state` in place
with no concurrency safety, so nothing here runs multiple roles in parallel. See
`specs/stories/EP-0008-Concurrency-Safe-State-And-A-Working-Parallel-Loop.md` for the concurrency
work that would be needed before that changes.

`watch_roadmap.py` is a small, optional, opt-in script (nothing else in this repo imports or runs it)
that polls the state repository for two things:

```bash
python3 watch_roadmap.py          # poll forever (Ctrl+C to stop)
python3 watch_roadmap.py --once   # check once and exit (0 if something fired, 1 if not) -
                                   # for wrapping in your own cron/systemd timer instead
```

- New commits on the configured develop branch (`GITHUB_DEVELOP_BRANCH`).
- A backlog item whose completed pipeline stages stop one short of the next one (e.g.
  Ready-but-not-Implemented - "ready for developers"; the same logic also catches
  Reviewed-but-not-Tested - "ready for QA", etc.).

When either fires, it prints a hard-to-miss banner (`WATCH_POLL_INTERVAL_SECONDS` in `.env` controls
the poll interval, default 300s) - it does **not** start or drive a session itself. You still start
the agent yourself (`python3 run.py`); this script only saves you from having to keep checking
whether there's anything new for it to do.

## Logging & Session Management

### Logging
The system uses Docker Compose for logging, which captures both orchestrator activity and sub-agent delegations.
- **View Real-time Logs**: `docker compose logs -f agent`
- **Verbosity**: The agent runs in `--verbose` mode by default, providing detailed traces of tool calls, LLM interactions, and state transitions.

### Session Management
Sessions are managed by the ADK framework to ensure continuity across restarts:
- **Persistence**: Conversation history and agent state are stored in the `sessions/` directory as `.session.json` files.
- **Session Identification**: Each run uses a `SESSION_ID` (defined in `.env`).
- **Resuming**: The `run.py` script automatically detects existing session files and uses the `--resume` flag to pick up where the team left off.
- **Interruption**: You can stop the agent at any time (e.g., by pressing `Ctrl+C` in interactive mode). The framework will automatically save the session state to a file on exit.
- **Metadata**: A shared SQLite database (`sessions/adk_sessions.db`) tracks session lifecycle and metadata, ensuring that even if the container is removed, the session history remains accessible.

## Diagnostics & Maintenance

### Doctor Script

The `doctor.py` script validates your local environment, ensuring Docker is running, the `.env` file is correctly configured, the state repository path is accessible, and the configured LLM provider/model is actually reachable and responding (a live test request through the LiteLLM proxy).

```bash
python3 doctor.py
```

It never stops at the first problem found - every check runs regardless of earlier ones, and
everything wrong is collected into a single "Actionable Items" punch list printed at the end, so you
see everything that needs fixing in one pass instead of fix-one/rerun/discover-the-next-one. Only
ERROR-level items block `run.py` from starting at all; WARNING-level items (e.g. `gh` not
authenticated) are shown but don't block anything.

Other scripts use this programmatically rather than just running the CLI: `run.py` calls
`doctor.check(...)` before attempting to start the agent (see above), and `setup_all.py` uses it as
the gate at the end of its guided flow, looping fix→retry until there are no more ERROR items.

For state-repository-specific checks (`specs/` structure, stray templates, `state.json` validity), see [State Repository § State Repository Check](STATE-REPOSITORY.md#state-repository-check).

### Rebuilding Images

`run.py`'s own image build only rebuilds Docker layers its cache considers stale - it never re-pulls
a mutable base image tag (`python:3.11-slim`, `ollama/ollama:latest`) on its own. To force a truly
fresh rebuild of this repo's own images (`agent`, plus `ollama` for a Local/Ollama setup):

```bash
python3 rebuild_images.py             # rebuild, pulling fresh base images
python3 rebuild_images.py --no-cache  # same, ignoring Docker's build cache entirely (slower)
```

`python3 run.py dev` does this automatically before every start, alongside verbose logging - see
above.

## Using the Scrum team agent

This repository provides the agent implementation under `agents/scrum_team/`. The package exports:

- `agents.scrum_team.root_agent`

Exactly how you *run* the agent depends on the host app / runner you plug it into (for example, an ADK-based runner). The key point is that `root_agent` is the entrypoint and it orchestrates the rest.
