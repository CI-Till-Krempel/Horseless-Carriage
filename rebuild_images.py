#!/usr/bin/env python3
"""
Rebuilds every Docker image this project builds itself: `agent`
(agent.Dockerfile, both compose files) always, plus `ollama`
(ollama.Dockerfile) when a Local/Ollama setup is active (see run.py's
compose_file_args). Pulls fresh base images by default (python:3.11-slim,
ollama/ollama:latest are both mutable tags).

Neither run.py nor setup_llm.py do this on their own: run.py's `--build`
only rebuilds layers Docker's own cache considers stale, which never
re-pulls a mutable base image tag by itself, and setup_llm.py never touches
the `agent` image at all - it only starts db/litellm(/ollama) to smoke-test
the LLM configuration. Use this script after a base-image update, a
Dockerfile change Docker's cache wouldn't otherwise consider dirty, or
whenever in doubt.

`db` (postgres:16-alpine) and `litellm` (a pulled LiteLLM release image) are
deliberately excluded - both use a pre-built image, not a Dockerfile this
repo owns, so there is nothing for `docker compose build` to do for them;
`docker compose pull` is the equivalent operation for those, not this
script's concern.

Usage:
  python3 rebuild_images.py             Rebuild agent (+ ollama if active), pulling fresh base images.
  python3 rebuild_images.py --no-cache  Same, but ignore Docker's build cache entirely (slower).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import run


def parse_args(argv: list) -> bool:
    """Returns whether --no-cache was passed."""
    return "--no-cache" in argv


def images_to_rebuild(compose_args: list) -> list:
    """Which of this repo's own build services need rebuilding for the
    active compose file - `agent` is defined in both compose files,
    `ollama` only in docker-compose.local.yaml (see run.py's
    compose_file_args, which this reuses to detect the active setup)."""
    services = ["agent"]
    if "docker-compose.local.yaml" in compose_args:
        services.append("ollama")
    return services


def main() -> None:
    os.chdir(Path(__file__).resolve().parent)
    no_cache = parse_args(sys.argv[1:])

    if shutil.which("docker") is None:
        print("ERROR: 'docker' command not found. Please install Docker.")
        sys.exit(1)

    compose_args = run.compose_file_args(Path("."))
    services = images_to_rebuild(compose_args)

    if compose_args:
        print(f"(Local/Ollama setup detected - using {compose_args[1]})")
    print(f"--- Rebuilding: {' + '.join(services)} ---")

    cmd = ["docker", "compose", *compose_args, "build", "--pull"]
    if no_cache:
        cmd.append("--no-cache")
    cmd.extend(services)

    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(result.returncode)

    print()
    print("Rebuild complete. Restart with: python3 run.py")


if __name__ == "__main__":
    main()
