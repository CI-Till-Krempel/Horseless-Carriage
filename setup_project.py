#!/usr/bin/env python3
"""
Setup script for the Horseless Carriage project.

This script will:
1. Check for Docker and Docker Compose.
2. Guide the user through GitHub CLI setup if needed.
3. Set up the .env file.
4. Start the database and LiteLLM containers.

(Named setup_project.py rather than setup.py to avoid colliding with the
setuptools convention of a package-root setup.py.)
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import lib_docker
import lib_env
import project_registry


def main() -> None:
    os.chdir(Path(__file__).resolve().parent)

    print("--- Running Horseless Carriage Setup ---")

    # 1. Check for Docker and Docker Compose
    if shutil.which("docker") is None:
        print("ERROR: 'docker' command not found. Please install Docker.")
        sys.exit(1)

    compose_ok = shutil.which("docker-compose") is not None
    if not compose_ok:
        try:
            subprocess.run(["docker", "compose", "version"], check=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            compose_ok = True
        except Exception:
            compose_ok = False
    if not compose_ok:
        print("ERROR: 'docker-compose' or 'docker compose' command not found. Please install Docker Compose.")
        sys.exit(1)

    # 2. Guide the user through GitHub CLI setup
    if shutil.which("gh") is None:
        print("WARNING: 'gh' command not found.")
        print("The GitHub CLI is recommended for the best experience.")
        print("Please visit https://cli.github.com/ to install it.")
    else:
        try:
            subprocess.run(["gh", "auth", "status"], check=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            print("WARNING: gh CLI is not authenticated.")
            print("Please run 'gh auth login' to authenticate with your GitHub account.")

    # 3. Set up .env file
    env_path = Path(".env")
    if not env_path.is_file():
        print("Creating .env file from .env.example...")
        shutil.copy(".env.example", env_path)
        print("IMPORTANT: Please edit the .env file to add your API keys and configuration.")
    else:
        print(".env file already exists.")

    # GH issue #482: running this script directly (skipping setup_llm.py's
    # interactive flow) used to leave LITELLM_MASTER_KEY/LITELLM_PROXY_API_KEY
    # as the literal ".env.example" placeholder text, which ADK then rejected
    # with "LiteLLM Virtual Key expected ... expected to start with 'sk-'".
    # Idempotent (no-op once a real value is already set) so this is safe to
    # run regardless of which setup script path a user takes, or how many
    # times.
    lib_env.ensure_master_key(env_path)
    lib_env.ensure_postgres_password(env_path)

    # Keeps project_registry.json's name/target_repo in sync with .env -
    # the same call new_project.py (initial install) and setup_llm.py
    # (every later reconfiguration) make, so a project set up/reconfigured
    # by running this script directly (skipping setup_llm.py) doesn't fall
    # out of sync with what dashboard.py shows. A no-op for a standalone
    # checkout or one with no STATE_REPO_PATH set yet.
    project_registry.sync_from_env(Path(".").resolve())

    # 4. Start the database and LiteLLM containers
    # Use the same compose file(s) setup_llm.py's own live test already
    # verified working (docker-compose.local.yaml [+ the GPU override] for
    # a Local/Ollama setup, docker-compose.yaml otherwise) - a bare
    # `docker compose up` here would target the default docker-compose.yaml
    # regardless, and since both compose files define a same-named
    # `litellm` service, Compose would recreate it against the WRONG
    # (cloud) config, silently undoing what setup_llm.py just configured
    # and verified (see SPEC-ISSUE-0028: a real Windows Local/Ollama setup_all.py
    # run hit exactly this - the subsequent doctor.py gate failed the live
    # test with "Missing Gemini API key", despite no cloud provider ever
    # being chosen).
    compose_args = lib_docker.compose_file_args(Path("."))
    # GH issue #169: same project name as run.py's dev stack (this just
    # starts db/litellm ahead of it), distinct from the ADK eval-set
    # runner's and the test suite's - see lib_docker.compose_project_args.
    full_compose_args = compose_args + lib_docker.compose_project_args("dev")
    # GH issue #288's own port auto-assignment only ever ran once, at
    # new_project.py install time (or dashboard.py's own Start action) -
    # running this script directly could otherwise bring up a stack whose
    # configured port another process has since taken, with no warning.
    # --build mirrors dashboard.py's own _start_stack: the image itself
    # doesn't embed a port, but a changed .env is exactly the kind of drift
    # a plain `up -d` with no rebuild can silently serve stale against.
    port_changes = lib_docker.ensure_dev_ports_available(Path("."), full_compose_args)
    print("Starting database and LiteLLM containers via Docker Compose...")
    if compose_args:
        print(f"(Local/Ollama setup detected - using {compose_args[1]})")
    up_cmd = ["docker", "compose", *full_compose_args, "up", "-d"]
    if port_changes:
        up_cmd.append("--build")
        summary = ", ".join(f"{var} {old} -> {new}" for var, old, new in port_changes)
        print(f"Port(s) reassigned ({summary}) - rebuilding before starting.")
    result = subprocess.run([*up_cmd, "db", "litellm"])
    if result.returncode != 0:
        sys.exit(result.returncode)

    print()
    print("--- Setup Complete ---")
    print("Next steps:")
    print("1. Edit the .env file with your specific API keys and settings.")
    print("2. Run the agent using: python3 run.py")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # GH issue #117 - see setup_llm.py's own handler for the full
        # rationale. Only wraps the standalone entry point - setup_all.py
        # calls main() directly and wants Ctrl+C to abort its whole guided
        # flow, not just this one step.
        print()
        print("Cancelled.")
        sys.exit(0)
