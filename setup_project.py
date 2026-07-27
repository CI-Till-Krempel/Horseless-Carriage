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

    # 4. Start the database and LiteLLM containers
    print("Starting database and LiteLLM containers via Docker Compose...")
    result = subprocess.run(["docker", "compose", "up", "-d", "db", "litellm"])
    if result.returncode != 0:
        sys.exit(result.returncode)

    print()
    print("--- Setup Complete ---")
    print("Next steps:")
    print("1. Edit the .env file with your specific API keys and settings.")
    print("2. Run the agent using: python3 run.py")


if __name__ == "__main__":
    main()
