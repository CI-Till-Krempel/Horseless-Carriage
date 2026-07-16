#!/bin/bash
#
# This script runs all tests (unit and integration) using Docker Compose.
#

set -e

echo "--- Running All Tests (Unit + Integration) ---"

# Tests never need real secrets (unit tests mock all external calls, and the
# integration test only talks to the local litellm proxy against a mocked
# model). Always run against .env.test so a real .env is never required or
# touched.
if [ ! -f ".env.test" ]; then
    echo "ERROR: .env.test file not found. It provides the mock values tests run against."
    exit 1
fi

# Run pytest inside the agent container with access to LiteLLM and DB services
# We override the default command to run pytest
docker compose --env-file .env.test run --rm -e PYTHONPATH=/app agent pytest -v --cov=agents agents/scrum_team/tests