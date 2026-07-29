"""
Shared Docker Compose lifecycle helpers for Horseless Carriage's host-side
run/setup scripts. Stdlib-only, works identically on macOS/Linux/Windows.
"""

import json
import re
import subprocess
from pathlib import Path
from typing import Optional

import lib_env
import lib_llm_test


def compose_file_args(repo_root: Path) -> list:
    """["-f", "docker-compose.local.yaml"] (plus ["-f", "docker-compose.gpu.yaml"]
    if OLLAMA_GPU_ENABLED=true in .env - see setup_llm.py's GPU prompt) for
    a Local/Ollama setup, else [] (default docker-compose.yaml). A
    Local/Ollama setup (see setup_llm.py's run_local_provider) only ever
    writes config/model-templates/litellm.local-ollama.yaml, never the root
    litellm.yaml docker-compose.yaml's litellm service mounts - it needs
    docker-compose.local.yaml (which mounts that file directly, and adds
    the ollama service) instead, or the agent comes up pointed at whichever
    cloud provider was last configured (or the repo's shipped default),
    with no matching API key set (GH issue #36).

    Lives here (rather than in run.py, where it originated) so both run.py
    and rebuild_images.py can call it without one importing the other -
    rebuild_images.py's developer-mode use from run.py would otherwise be
    a circular import."""
    repo_root = Path(repo_root)
    active_provider = lib_llm_test.llm_active_provider(lib_llm_test.llm_active_config_path(repo_root))
    if active_provider != "local":
        return []
    args = ["-f", "docker-compose.local.yaml"]
    if lib_env.read_env_var(repo_root / ".env", "OLLAMA_GPU_ENABLED") == "true":
        args += ["-f", "docker-compose.gpu.yaml"]
    return args


def compose_running_services(compose_args: list) -> list:
    """Names of services with at least one running container for the
    compose project resolved from compose_args + the current directory -
    via `docker compose <compose_args> ps --status running`. Empty if
    nothing is running, docker/compose isn't available, or the check
    itself fails for any reason - this is a best-effort diagnostic, never
    a hard gate on actually starting the stack."""
    cmd = ["docker", "compose", *compose_args, "ps", "--status", "running", "--format", "json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception:
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    services = []
    for line in result.stdout.strip().splitlines():
        # Different Compose versions emit either one JSON array for the
        # whole project or one JSON object per line - handle both shapes.
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        for entry in (parsed if isinstance(parsed, list) else [parsed]):
            name = entry.get("Service")
            if name:
                services.append(name)
    return services


def ollama_gpu_status(compose_args: list) -> Optional[str]:
    """"cuda" or "cpu", per Ollama's own "inference compute" startup log
    line (see docs/SETUP.md's "GPU Support" section) - or None if this
    can't be determined right now (ollama not running yet, hasn't logged
    that line yet, or docker itself is unavailable). A GPU override file
    merged into the compose command is no guarantee the GPU is actually
    reachable from inside the container (wrong/missing driver, WSL2 not
    enabled, etc.) - Docker starts the container either way and Ollama
    silently falls back to CPU - so this is the only way to tell the two
    apart short of a human reading `docker compose logs ollama` by hand.
    Best-effort diagnostic only, never a hard gate."""
    cmd = ["docker", "compose", *compose_args, "logs", "ollama"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    matches = re.findall(r"inference compute.*?library=(\w+)", result.stdout + result.stderr)
    return matches[-1] if matches else None


def maybe_stop_existing_stack(compose_args: list) -> None:
    """If this compose project already has running containers, tells the
    user and asks whether to stop+recreate (`docker compose <compose_args>
    down`) before the caller's own `up` proceeds - a controlled way out of
    a stale/conflicting leftover stack (e.g. from switching between
    docker-compose.yaml and docker-compose.local.yaml, which share the
    same default project name and several service names, or a container
    left in a broken state from an earlier interrupted run) instead of
    `docker compose up` failing outright with no obvious cause.

    Declining (the default) leaves the existing containers alone and just
    lets the caller's own `up` call reconcile them exactly as it would
    have without this check - this never blocks or replaces that call,
    it only offers an optional clean-slate reset beforehand."""
    running = compose_running_services(compose_args)
    if not running:
        return
    print(f"An existing Horseless Carriage stack looks like it's already running ({', '.join(sorted(set(running)))}).")
    answer = input("Stop and recreate it before starting? [y/N]: ").strip().lower()
    if not answer.startswith("y"):
        return
    print("Stopping the existing stack...")
    try:
        result = subprocess.run(["docker", "compose", *compose_args, "down"])
        if result.returncode != 0:
            print("WARNING: 'docker compose down' did not complete cleanly - proceeding anyway.")
    except Exception as e:
        print(f"WARNING: 'docker compose down' did not complete cleanly ({e}) - proceeding anyway.")
