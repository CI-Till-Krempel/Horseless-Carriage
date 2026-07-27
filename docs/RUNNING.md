[← Back to README](../README.md)

# Running the Agent

Run the agent using the `run.py` script:

```bash
python3 run.py
```

This script will:
1. Load environment variables from `.env`.
2. Check for the existence of the state repository path.
3. Build and run the agent container.
4. Wait for the LiteLLM dashboard (and, in web mode, the ADK web UI) to come up, then open them in your default browser.

`run.py` supports three modes, which can be combined:

| Command | Behavior |
|---|---|
| `python3 run.py` | **Default.** ADK web frontend, foreground, at `http://localhost:8000`. |
| `python3 run.py cli [query...]` | Interactive CLI session in your terminal instead of the web UI. |
| `python3 run.py daemon` | Add to either of the above to run detached (`python3 run.py daemon` or `python3 run.py cli daemon`). |

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

For state-repository-specific checks (`specs/` structure, stray templates, `state.json` validity), see [State Repository § State Repository Check](STATE-REPOSITORY.md#state-repository-check).

## Using the Scrum team agent

This repository provides the agent implementation under `agents/scrum_team/`. The package exports:

- `agents.scrum_team.root_agent`

Exactly how you *run* the agent depends on the host app / runner you plug it into (for example, an ADK-based runner). The key point is that `root_agent` is the entrypoint and it orchestrates the rest.
