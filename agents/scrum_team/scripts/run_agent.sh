#!/bin/bash
# agents/scrum_team/scripts/run_agent.sh
set -e

# This script is intended to run INSIDE the container.
# It automatically handles session resumption if a session file exists.

SESSION_ID=${SESSION_ID:-my-sprint}
SESSION_FILE="/app/sessions/${SESSION_ID}.session.json"
DB_FILE="/app/sessions/adk_sessions.db"
LOG_LEVEL_VAL=${LOG_LEVEL:-info}

# Ensure sessions directory exists (though it should be mounted)
mkdir -p /app/sessions

# Base arguments for adk run
# We use the persistent SQLite DB in /app/sessions to preserve event history
# We use --save_session to snapshot the agent state on exit
CMD_ARGS="--session_service_uri sqlite:///${DB_FILE} --log_level ${LOG_LEVEL_VAL} --save_session --session_id /app/sessions/${SESSION_ID}"

# Check if session file exists to resume
RESUME_ARG=""
if [ -f "$SESSION_FILE" ]; then
    # Basic check if file is valid JSON and not empty
    if [ -s "$SESSION_FILE" ] && jq '.' "$SESSION_FILE" >/dev/null 2>&1; then
        echo "--- Found existing session file: $SESSION_FILE ---"
        echo "--- Resuming session: $SESSION_ID ---"
        RESUME_ARG="--resume $SESSION_FILE"
    else
        echo "--- WARNING: Session file $SESSION_FILE is empty or corrupted. Starting a new session. ---"
        # Move corrupted file aside
        mv "$SESSION_FILE" "${SESSION_FILE}.corrupted.$(date +%s)"
    fi
else
    echo "--- No existing session file found for ID: $SESSION_ID ---"
    echo "--- Starting a new session: $SESSION_ID ---"
fi

# Run adk
# "$@" allows passing additional arguments (like a query)
echo "Executing: adk run $RESUME_ARG $CMD_ARGS agents $@"
exec adk run $RESUME_ARG $CMD_ARGS agents "$@"
