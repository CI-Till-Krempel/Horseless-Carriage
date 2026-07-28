"""
Shared Docker Compose lifecycle helpers for Horseless Carriage's host-side
run/setup scripts. Stdlib-only, works identically on macOS/Linux/Windows.
"""

import json
import subprocess


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
