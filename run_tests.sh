#!/bin/bash
#
# This script runs all tests (unit and integration) using Docker Compose.
#

set -e

echo "--- Running All Tests (Unit + Integration) ---"

# Ensure the environment is set up (needed for docker-compose)
if [ ! -f ".env" ]; then
    echo "ERROR: .env file not found. Please create it (use .env.example as a template)."
    exit 1
fi

# Run pytest inside the agent container with access to LiteLLM and DB services
# We override the default command to run pytest
docker compose run --rm -e PYTHONPATH=/app agent pytest -v --cov=agents agents/scrum_team/tests