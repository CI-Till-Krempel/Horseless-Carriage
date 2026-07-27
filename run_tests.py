#!/usr/bin/env python3
"""
Runs all tests (unit and integration) using Docker Compose.

Tests never need real secrets (unit tests mock all external calls, and the
integration test only talks to the local litellm proxy against a mocked
model). Always run against .env.test so a real .env is never required or
touched.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import lib_env


def main() -> None:
    os.chdir(Path(__file__).resolve().parent)
    print("--- Running All Tests (Unit + Integration) ---")

    if shutil.which("docker") is None:
        print("ERROR: 'docker' command not found. Please install Docker.")
        sys.exit(1)

    env_test_path = Path(".env.test")
    if not env_test_path.is_file():
        print("ERROR: .env.test file not found. It provides the mock values tests run against.")
        sys.exit(1)

    # The state-repo path is bind-mounted by docker-compose; some Docker
    # hosts (e.g. Docker Desktop) fail outright if the source directory
    # doesn't exist yet, instead of auto-creating it. Ensure it's there
    # before starting.
    state_repo_test_path = lib_env.read_env_var(env_test_path, "STATE_REPO_PATH")
    if state_repo_test_path:
        Path(state_repo_test_path).expanduser().mkdir(parents=True, exist_ok=True)

    # Run pytest inside the agent container with access to LiteLLM and DB
    # services. --entrypoint "" skips entrypoint.sh (GitHub CLI auth, git
    # config): the test suite mocks every external call and has no need for
    # it, and .env.test's GITHUB_TOKEN is a fake value that would otherwise
    # fail real auth.
    cmd = [
        "docker", "compose", "--env-file", ".env.test", "run", "--rm",
        "--entrypoint", "", "-e", "PYTHONPATH=/app", "agent",
        "pytest", "-v", "--cov=agents", "agents/scrum_team/tests",
    ]
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
