#!/usr/bin/env python3
"""
Run script for the Horseless Carriage project.

This script will:
1. Use doctor.py as a gatekeeper: refuse to start if the configuration has
   any blocking problem (missing .env, no state repo, etc.) - see
   doctor.check().
2. Build and run the agent container with session management and logging.
3. Wait for the dashboards to come up and open them in your default browser.

Usage:
  python3 run.py                 Web mode (default): ADK web frontend, foreground.
  python3 run.py cli [query...]  Interactive CLI session instead of the web UI.
  python3 run.py daemon          Add to either of the above to run detached.
  python3 run.py dev             Add to either of the above for developer mode:
                                  rebuilds agent/ollama images fresh before
                                  starting (see rebuild_images.py) and runs
                                  with LOG_LEVEL=debug for this invocation.
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

import banner
import doctor
import lib_docker
import rebuild_images

LITELLM_DASHBOARD_URL = "http://localhost:4000/ui"
ADK_WEB_URL = "http://localhost:8000"


def parse_args(argv):
    mode = "web"
    daemon = False
    dev = False
    extra = []
    for arg in argv:
        if arg == "web":
            mode = "web"
        elif arg == "cli":
            mode = "cli"
        elif arg == "daemon":
            daemon = True
        elif arg == "dev":
            dev = True
        else:
            extra.append(arg)
    return mode, daemon, dev, extra


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
    """Kept as a thin re-export so existing callers/tests can keep
    referencing run.compose_file_args - the actual logic now lives in
    lib_docker.compose_file_args (shared with rebuild_images.py's
    developer-mode use from this module, which would otherwise need a
    circular import)."""
    return lib_docker.compose_file_args(repo_root)


def main(argv: list = None) -> None:
    """argv defaults to sys.argv[1:] - callers like setup_all.py that want to
    hand off to this directly (e.g. after a guided setup, with a chosen
    mode/dev flag) can pass an explicit list instead of mutating
    sys.argv themselves."""
    try:
        _main(argv)
    except KeyboardInterrupt:
        # GH issue #74: Ctrl+C during the foreground `docker compose up`
        # below raised a raw, uncaught KeyboardInterrupt all the way out of
        # subprocess.run() (on at least one real Windows run, from inside
        # subprocess.communicate()'s own wait) - a crash-looking traceback
        # for what both of this function's own "Press Ctrl+C to stop"
        # messages describe as the normal, expected way to end a foreground
        # run. Treat it as one: a clean message and a non-error exit code,
        # not a stack trace.
        print()
        print("Stopped.")
        sys.exit(0)


def _main(argv: list = None) -> None:
    os.chdir(Path(__file__).resolve().parent)
    banner.print_banner()
    mode, daemon, dev, extra_args = parse_args(sys.argv[1:] if argv is None else argv)

    if shutil.which("docker") is None:
        print("ERROR: 'docker' command not found. Please install Docker.")
        sys.exit(1)

    # doctor.py is the gatekeeper: don't even try to start the stack if the
    # configuration itself is broken. skip_llm_probe=True since nothing's
    # running yet - a live proxy-reachability check here could only ever
    # report "not reachable" and would cost several real seconds for
    # nothing (see doctor.check()'s docstring).
    result = doctor.check(Path("."), skip_llm_probe=True)
    if result.has_errors:
        print()
        print("Cannot start: fix the ERROR items above, then try again.")
        print("(python3 doctor.py for full details, or python3 setup_all.py to fix them interactively.)")
        sys.exit(1)

    compose_args = compose_file_args(Path("."))
    # GH issue #169: give this stack its own Compose project name so its
    # containers/images (horseless-carriage-dev-*) can't be confused with
    # the ADK eval-set runner's or the test suite's - see
    # lib_docker.compose_project_args.
    full_compose_args = compose_args + lib_docker.compose_project_args("dev")

    print(f"--- Starting Horseless Carriage agent via Docker Compose (mode: {mode}) ---")
    if compose_args:
        print(f"(Local/Ollama setup detected - using {compose_args[1]})")

    if dev:
        print("--- Developer mode: rebuilding images before starting ---")
        rebuild_exit_code = rebuild_images.rebuild(full_compose_args)
        if rebuild_exit_code != 0:
            sys.exit(rebuild_exit_code)

    proc_env = os.environ.copy()
    proc_env["AGENT_MODE"] = mode
    if dev:
        proc_env["LOG_LEVEL"] = "debug"
        print("--- Developer mode: LOG_LEVEL overridden to 'debug' for this run ---")

    if mode == "cli":
        if daemon:
            print("NOTE: 'cli' mode needs an interactive terminal; ignoring 'daemon'.")
        print("Running agent in interactive CLI mode. Press Ctrl+C to exit.")
        thread = threading.Thread(target=open_dashboards, args=(mode,), daemon=True)
        thread.start()
        # Resumption logic is handled internally by the container's run_agent.sh script.
        cmd = ["docker", "compose", *full_compose_args, "run", "--rm", "--build", "agent",
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
        lib_docker.maybe_stop_existing_stack(full_compose_args)

        thread = threading.Thread(target=open_dashboards, args=(mode,), daemon=True)
        thread.start()

        if daemon:
            result = subprocess.run(["docker", "compose", *full_compose_args, "up", "-d", "--build", "agent"], env=proc_env)
            if result.returncode != 0:
                sys.exit(result.returncode)
            thread.join()
            print("Agent container started in daemon mode.")
            logs_cmd = " ".join(["docker", "compose", *full_compose_args, "logs", "-f", "agent"])
            print(f"To view logs, run: {logs_cmd}")
        else:
            print("Running ADK web frontend in foreground. Press Ctrl+C to stop.")
            result = subprocess.run(["docker", "compose", *full_compose_args, "up", "--build", "agent"], env=proc_env)
            sys.exit(result.returncode)


if __name__ == "__main__":
    main()
