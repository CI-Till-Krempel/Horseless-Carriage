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

# The state-repo path is bind-mounted by docker-compose; some Docker hosts
# (e.g. Docker Desktop) fail outright if the source directory doesn't exist
# yet, instead of auto-creating it. Ensure it's there before starting.
STATE_REPO_TEST_PATH=$(grep -E '^STATE_REPO_PATH=' .env.test | cut -d '=' -f2- | tr -d '"')
if [ -n "$STATE_REPO_TEST_PATH" ]; then
    mkdir -p "$STATE_REPO_TEST_PATH"
fi

# Run pytest inside the agent container with access to LiteLLM and DB services.
# --entrypoint "" skips entrypoint.sh (GitHub CLI auth, git config): the test
# suite mocks every external call and has no need for it, and .env.test's
# GITHUB_TOKEN is a fake value that would otherwise fail real auth.
docker compose --env-file .env.test run --rm --entrypoint "" -e PYTHONPATH=/app agent pytest -v --cov=agents agents/scrum_team/tests