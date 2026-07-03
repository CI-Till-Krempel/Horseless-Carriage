#!/bin/bash
#
# Run script for the Horseless Carriage project.
#
# This script will:
# 1. Load environment variables from .env.
# 2. Check for the existence of the state repository path.
# 3. Build and run the agent container using Docker Compose.
#

set -e

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

echo "--- Starting Horseless Carriage agent via Docker Compose ---"
echo ""
echo "  LiteLLM UI: http://localhost:4000/ui"
echo ""

# 3. Build and run the agent container
#    The `-d` flag will be passed to `docker compose up` if the first argument is "daemon"
if [ "$1" == "daemon" ]; then
    shift
    docker compose up -d --build agent "$@"
    echo "Agent container started in daemon mode."
    echo "To view logs, run: docker compose logs -f agent"
else
    echo "Running agent in interactive mode. Press Ctrl+C to exit."
    # Use `docker compose run` for interactive sessions
    docker compose run --rm --build agent
fi