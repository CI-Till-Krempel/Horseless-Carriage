#!/usr/bin/env python3
"""
Run script for the Horseless Carriage project.

This script will:
1. Load environment variables from .env.
2. Check for the existence of the state repository path.
3. Build and run the agent container with session management and logging.
4. Wait for the dashboards to come up and open them in your default browser.

Usage:
  python3 run.py                 Web mode (default): ADK web frontend, foreground.
  python3 run.py cli [query...]  Interactive CLI session instead of the web UI.
  python3 run.py daemon          Add to either of the above to run detached.
"""

import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import lib_docker
import lib_env
import lib_llm_test

LITELLM_DASHBOARD_URL = "http://localhost:4000/ui"
ADK_WEB_URL = "http://localhost:8000"


def parse_args(argv):
    mode = "web"
    daemon = False
    extra = []
    for arg in argv:
        if arg == "web":
            mode = "web"
        elif arg == "cli":
            mode = "cli"
        elif arg == "daemon":
            daemon = True
        else:
            extra.append(arg)
    return mode, daemon, extra


def wait_for_http(url: str, tries: int = 30) -> bool:
    """Polls a URL until it responds with 2xx or the tries run out."""
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if 200 <= resp.status < 300:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def open_url(url: str) -> None:
    """Opens a URL in the OS default browser, best-effort (works identically
    on macOS/Linux/Windows via the stdlib - no per-OS branching needed)."""
    if not webbrowser.open(url):
        print(f"Open manually: {url}")


def open_dashboards(mode: str) -> None:
    """Waits for each dashboard to become reachable, then opens it in the
    browser. Runs in a background thread so it doesn't block the foreground
    container output."""
    if wait_for_http("http://localhost:4000/health/readiness"):
        print(f"--- LiteLLM dashboard ready: {LITELLM_DASHBOARD_URL} ---")
        open_url(LITELLM_DASHBOARD_URL)
    else:
        print(f"WARNING: LiteLLM dashboard did not become ready in time. Open manually: {LITELLM_DASHBOARD_URL}")

    if mode == "web":
        if wait_for_http(ADK_WEB_URL):
            print(f"--- ADK web frontend ready: {ADK_WEB_URL} ---")
            open_url(ADK_WEB_URL)
        else:
            print(f"WARNING: ADK web frontend did not become ready in time. Open manually: {ADK_WEB_URL}")


def compose_file_args(repo_root: Path) -> list:
    """["-f", "docker-compose.local.yaml"] if a Local/Ollama setup is
    active, else [] (default docker-compose.yaml). A Local/Ollama setup
    (see setup_llm.py's run_local_provider) only ever writes
    config/model-templates/litellm.local-ollama.yaml, never the root
    litellm.yaml docker-compose.yaml's litellm service mounts - it needs
    docker-compose.local.yaml (which mounts that file directly, and adds
    the ollama service) instead, or the agent comes up pointed at whichever
    cloud provider was last configured (or the repo's shipped default),
    with no matching API key set (GH issue #36)."""
    active_provider = lib_llm_test.llm_active_provider(lib_llm_test.llm_active_config_path(repo_root))
    return ["-f", "docker-compose.local.yaml"] if active_provider == "local" else []


def main() -> None:
    os.chdir(Path(__file__).resolve().parent)
    mode, daemon, extra_args = parse_args(sys.argv[1:])

    if shutil.which("docker") is None:
        print("ERROR: 'docker' command not found. Please install Docker.")
        sys.exit(1)

    # 1. Load environment variables from .env
    env_path = Path(".env")
    if not env_path.is_file():
        print("ERROR: .env file not found. Please copy .env.example to .env and fill in the values.")
        sys.exit(1)
    print("Loaded environment variables from .env")

    # 2. Check for the existence of the state repository path
    state_repo_path = lib_env.read_env_var(env_path, "STATE_REPO_PATH")
    if not state_repo_path or not Path(state_repo_path).expanduser().is_dir():
        print(f"ERROR: STATE_REPO_PATH is not set or the directory does not exist: {state_repo_path}")
        print("Please create this directory and ensure it is correctly set in your .env file.")
        sys.exit(1)

    compose_args = compose_file_args(Path("."))

    print(f"--- Starting Horseless Carriage agent via Docker Compose (mode: {mode}) ---")
    if compose_args:
        print(f"(Local/Ollama setup detected - using {compose_args[1]})")

    proc_env = os.environ.copy()
    proc_env["AGENT_MODE"] = mode

    if mode == "cli":
        if daemon:
            print("NOTE: 'cli' mode needs an interactive terminal; ignoring 'daemon'.")
        print("Running agent in interactive CLI mode. Press Ctrl+C to exit.")
        thread = threading.Thread(target=open_dashboards, args=(mode,), daemon=True)
        thread.start()
        # Resumption logic is handled internally by the container's run_agent.sh script.
        cmd = ["docker", "compose", *compose_args, "run", "--rm", "--build", "agent",
               "/bin/bash", "/app/agents/scrum_team/scripts/run_agent.sh", *extra_args]
        result = subprocess.run(cmd, env=proc_env)
        sys.exit(result.returncode)
    else:
        # A leftover stack from an earlier run (or from switching between
        # docker-compose.yaml and docker-compose.local.yaml, which share
        # the same default project name and several service names) can
        # make `docker compose up` fail outright with no obvious cause -
        # offer a controlled reset before that happens (GH discussion on
        # local Ollama setups).
        lib_docker.maybe_stop_existing_stack(compose_args)

        thread = threading.Thread(target=open_dashboards, args=(mode,), daemon=True)
        thread.start()

        if daemon:
            result = subprocess.run(["docker", "compose", *compose_args, "up", "-d", "--build", "agent"], env=proc_env)
            if result.returncode != 0:
                sys.exit(result.returncode)
            thread.join()
            print("Agent container started in daemon mode.")
            logs_cmd = " ".join(["docker", "compose", *compose_args, "logs", "-f", "agent"])
            print(f"To view logs, run: {logs_cmd}")
        else:
            print("Running ADK web frontend in foreground. Press Ctrl+C to stop.")
            result = subprocess.run(["docker", "compose", *compose_args, "up", "--build", "agent"], env=proc_env)
            sys.exit(result.returncode)


if __name__ == "__main__":
    main()
