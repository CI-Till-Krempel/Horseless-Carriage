#!/bin/bash
#
# Run script for the Horseless Carriage project.
#
# This script will:
# 1. Load environment variables from .env.
# 2. Check for the existence of the state repository path.
# 3. Build and run the agent container with session management and logging.
# 4. Wait for the dashboards to come up and open them in your default browser.
#
# Usage:
#   ./run.sh                 Web mode (default): ADK web frontend, foreground.
#   ./run.sh cli [query...]  Interactive CLI session (previous default behavior).
#   ./run.sh daemon          Run detached (works with either mode above).

set -e

MODE="web"
DAEMON=false
ARGS=()
for arg in "$@"; do
    case "$arg" in
        web) MODE="web" ;;
        cli) MODE="cli" ;;
        daemon) DAEMON=true ;;
        *) ARGS+=("$arg") ;;
    esac
done

# 1. Load environment variables from .env
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
    echo "Loaded environment variables from .env"
else
    echo "ERROR: .env file not found. Please copy .env.example to .env and fill in the values."
    exit 1
fi

# 2. Check for the existence of the state repository path
if [ -z "$STATE_REPO_PATH" ] || [ ! -d "$STATE_REPO_PATH" ]; then
    echo "ERROR: STATE_REPO_PATH is not set or the directory does not exist: $STATE_REPO_PATH"
    echo "Please create this directory and ensure it is correctly set in your .env file."
    exit 1
fi

export AGENT_MODE="$MODE"

LITELLM_DASHBOARD_URL="http://localhost:4000/ui"
ADK_WEB_URL="http://localhost:8000"

echo "--- Starting Horseless Carriage agent via Docker Compose (mode: ${MODE}) ---"

# Opens a URL in the OS default browser, best-effort.
open_url() {
    local url="$1"
    case "$(uname -s)" in
        Darwin) command -v open >/dev/null 2>&1 && open "$url" ;;
        Linux) command -v xdg-open >/dev/null 2>&1 && xdg-open "$url" >/dev/null 2>&1 ;;
        MINGW*|MSYS*|CYGWIN*) command -v cmd.exe >/dev/null 2>&1 && cmd.exe /c start "" "$url" ;;
        *) return 1 ;;
    esac
}

# Polls a URL until it responds or the timeout is hit.
wait_for_http() {
    local url="$1" tries=30
    while [ "$tries" -gt 0 ]; do
        if curl --silent --fail --max-time 2 "$url" >/dev/null 2>&1; then
            return 0
        fi
        tries=$((tries - 1))
        sleep 1
    done
    return 1
}

# Waits for each dashboard to become reachable, then opens it in the browser.
# Runs in the background so it doesn't block the foreground container output.
(
    if wait_for_http "http://localhost:4000/health/readiness"; then
        echo "--- LiteLLM dashboard ready: ${LITELLM_DASHBOARD_URL} ---"
        open_url "$LITELLM_DASHBOARD_URL" || echo "Open manually: ${LITELLM_DASHBOARD_URL}"
    else
        echo "WARNING: LiteLLM dashboard did not become ready in time. Open manually: ${LITELLM_DASHBOARD_URL}"
    fi

    if [ "$MODE" == "web" ]; then
        if wait_for_http "$ADK_WEB_URL"; then
            echo "--- ADK web frontend ready: ${ADK_WEB_URL} ---"
            open_url "$ADK_WEB_URL" || echo "Open manually: ${ADK_WEB_URL}"
        else
            echo "WARNING: ADK web frontend did not become ready in time. Open manually: ${ADK_WEB_URL}"
        fi
    fi
) &

if [ "$MODE" == "cli" ]; then
    if [ "$DAEMON" == true ]; then
        echo "NOTE: 'cli' mode needs an interactive terminal; ignoring 'daemon'."
    fi
    echo "Running agent in interactive CLI mode. Press Ctrl+C to exit."
    # Use `docker compose run` for interactive sessions
    # Resumption logic is handled internally by the container's run_agent.sh script
    docker compose run --rm --build agent /bin/bash /app/agents/scrum_team/scripts/run_agent.sh "${ARGS[@]}"
else
    if [ "$DAEMON" == true ]; then
        docker compose up -d --build agent
        wait
        echo "Agent container started in daemon mode."
        echo "To view logs, run: docker compose logs -f agent"
    else
        echo "Running ADK web frontend in foreground. Press Ctrl+C to stop."
        docker compose up --build agent
    fi
fi