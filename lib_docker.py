"""
Shared Docker Compose lifecycle helpers for Horseless Carriage's host-side
run/setup scripts. Stdlib-only, works identically on macOS/Linux/Windows.
"""

import json
import re
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import lib_env
import lib_llm_test


def compose_file_args(repo_root: Path) -> list:
    """["-f", "docker-compose.local.yaml"] (plus ["-f", "docker-compose.gpu.yaml"]
    if OLLAMA_GPU_ENABLED=true - see setup_llm.py's GPU prompt), or ["-f",
    "docker-compose.local-hostollama.yaml"] instead of both if
    OLLAMA_HOST_MODE=true (GH issue #93: Ollama running natively on the host
    machine instead of in a container - see setup_llm.py's macOS prompt) -
    for a Local/Ollama setup, else [] (default docker-compose.yaml). A
    Local/Ollama setup (see setup_llm.py's run_local_provider) only ever
    writes config/model-templates/litellm.local-ollama.yaml, never the root
    litellm.yaml docker-compose.yaml's litellm service mounts - it needs
    docker-compose.local.yaml (which mounts that file directly, and adds
    the ollama service) instead, or the agent comes up pointed at whichever
    cloud provider was last configured (or the repo's shipped default),
    with no matching API key set (GH issue #36).

    Host mode uses a wholly separate compose file, not docker-compose.local
    .yaml plus an overlay like the GPU case: Compose merges (rather than
    replaces) `depends_on` across `-f` files, so an overlay can't actually
    remove litellm's dependency on the dockerized `ollama` service - see
    docker-compose.local-hostollama.yaml's own header comment. The two are
    mutually exclusive (host mode bypasses the dockerized `ollama` service -
    and therefore its GPU override - entirely; setup_llm.py never writes
    both flags true), but host mode still wins here if it somehow did,
    since it's the more fundamental of the two.

    Lives here (rather than in run.py, where it originated) so both run.py
    and rebuild_images.py can call it without one importing the other -
    rebuild_images.py's developer-mode use from run.py would otherwise be
    a circular import."""
    repo_root = Path(repo_root)
    active_provider = lib_llm_test.llm_active_provider(lib_llm_test.llm_active_config_path(repo_root))
    if active_provider != "local":
        return []
    env_path = repo_root / ".env"
    if lib_env.read_env_var(env_path, "OLLAMA_HOST_MODE") == "true":
        return ["-f", "docker-compose.local-hostollama.yaml"]
    args = ["-f", "docker-compose.local.yaml"]
    if lib_env.read_env_var(env_path, "OLLAMA_GPU_ENABLED") == "true":
        args += ["-f", "docker-compose.gpu.yaml"]
    return args


def compose_project_args(context: str, repo_root: Optional[Path] = None) -> list:
    """["-p", f"{prefix}-{context}"] (prefix defaults to "horseless-carriage")
    - GH issue #169: with no explicit project name, Compose derives one from
    the checkout directory's basename alone, so the dev stack (run.py/
    setup_project.py/setup_llm.py), the ADK eval-set runner (run_adk_eval.py)
    and the test suite (run_tests.py) all resolve to the SAME project name -
    and therefore the same container names (e.g. horseless-carriage-db-1)
    and built-image names - even though they're independent stacks. That
    makes it impossible to tell which container belongs to which if more
    than one runs at the same time on the same machine (and risks one
    silently adopting/recreating another's containers). context is one of
    "dev", "eval", "test".

    GH issue #288: prefix itself is also overridable, via COMPOSE_PROJECT_
    NAME_PREFIX in <repo_root>/.env - new_project.py sets this automatically
    when installing Horseless Carriage as a submodule of a target repo, so
    two DIFFERENT target projects (each their own HC submodule + .env) don't
    collide on container/image names the same way #169 fixed collisions
    between contexts within one checkout.

    repo_root defaults to the current working directory - every caller
    except doctor.py has already os.chdir()'d to the repo root by the time
    this runs (doctor.py's own check()/run() take repo_root explicitly - see
    its own docstring - and must pass it here too, or a configured prefix
    would silently not apply when doctor.py is invoked with a different
    cwd)."""
    repo_root = Path(repo_root) if repo_root is not None else Path(".")
    prefix = lib_env.read_env_var(repo_root / ".env", "COMPOSE_PROJECT_NAME_PREFIX") or "horseless-carriage"
    return ["-p", f"{prefix}-{context}"]


def compose_running_services(compose_args: list, cwd: Optional[Path] = None) -> list:
    """Names of services with at least one running container for the
    compose project resolved from compose_args + cwd (defaults to the
    current directory, matching every caller's own os.chdir()'d-to-repo-
    root invariant) - via `docker compose <compose_args> ps --status
    running`. Empty if nothing is running, docker/compose isn't available,
    or the check itself fails for any reason - this is a best-effort
    diagnostic, never a hard gate on actually starting the stack.

    GH issue #310: dashboard.py is the one caller that genuinely needs an
    explicit cwd - it checks several *different* projects' stacks in the
    same process (no single repo-root chdir applies), one right after
    another."""
    cmd = ["docker", "compose", *compose_args, "ps", "--status", "running", "--format", "json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, cwd=cwd)
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


def host_ollama_reachable(base_url: str = "http://localhost:11434", timeout_secs: int = 3) -> bool:
    """GH issue #93: in host-Ollama mode (OLLAMA_HOST_MODE=true,
    docker-compose.local-hostollama.yaml) there is no `ollama` container for
    doctor.py's other checks to inspect - Ollama runs directly on the host,
    so this is checked the same way a human would: a plain HTTP request
    against Ollama's own root endpoint (which responds "Ollama is running"
    on success), from the host itself rather than from inside a container.
    Never raises - unreachable, not installed, or not started yet all just
    mean "no", not a setup error."""
    try:
        with urllib.request.urlopen(base_url, timeout=timeout_secs) as resp:
            return resp.status == 200
    except Exception:
        return False


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


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """True if nothing on this machine currently has `port` bound - used both
    at install time (new_project.py) and at every actual stack startup
    (ensure_dev_ports_available below) to decide whether LITELLM_HOST_PORT/
    AGENT_WEB_HOST_PORT need reassigning."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def find_free_port(preferred: int, max_attempts: int = 50) -> Optional[int]:
    """preferred if free, else the first free port scanning upward - so a
    second/third concurrent installation gets its own port instead of
    failing to bind at `docker compose up` time with no clear reason why.
    Returns None (rather than raising/exiting) if nothing was free in range -
    callers with different error-handling needs (new_project.py's one-shot
    interactive setup dies outright; run.py/dashboard.py's startup checks
    below just skip reassignment and let the real `docker compose up`
    failure, if any, surface normally) decide how to react themselves."""
    for port in range(preferred, preferred + max_attempts):
        if is_port_free(port):
            return port
    return None


_DEV_HOST_PORT_VARS = (("LITELLM_HOST_PORT", 4000), ("AGENT_WEB_HOST_PORT", 8000))


def ensure_dev_ports_available(repo_root: Path, compose_args: list) -> list:
    """Re-checks LITELLM_HOST_PORT/AGENT_WEB_HOST_PORT immediately before a
    stack actually starts (not just once at new_project.py install time) -
    GH issue #288's own port auto-assignment only ever ran then, so two
    projects installed back-to-back while neither's stack was running yet
    could still end up configured with the same port, surfacing only as an
    opaque Docker bind failure once both were actually started.

    No-op (returns []) if this project's own stack is already running
    (compose_running_services non-empty) - a port this project is already
    bound to isn't a conflict with "another" installation, it's just itself
    continuing to run; without this check, re-running `run.py` against an
    already-up stack would wrongly treat its own ports as taken and
    reassign them out from under it.

    Otherwise, for each var: if its current (or default) port is genuinely
    free, leave it; if something else now holds it, find a new free one and
    persist it via lib_env.update_env_var. Returns a list of
    (var, old_port, new_port) for whatever actually changed - empty if
    nothing did, so callers (run.py/dashboard.py) know whether a rebuild is
    warranted."""
    if compose_running_services(compose_args, cwd=repo_root):
        return []

    env_path = Path(repo_root) / ".env"
    changes = []
    for var, default_port in _DEV_HOST_PORT_VARS:
        current = lib_env.read_env_var(env_path, var)
        port = int(current) if current else default_port
        if is_port_free(port):
            continue
        new_port = find_free_port(port + 1)
        if new_port is None:
            continue  # best-effort; the real `docker compose up` bind failure (if any) will surface on its own
        lib_env.update_env_var(env_path, var, str(new_port))
        changes.append((var, port, new_port))
    return changes


def print_stack_conflict_hint(project_args: list) -> None:
    """GH issue #232: called after a `docker compose up` failure to print a
    non-interactive pointer at the likely cause, instead of just leaving
    Docker's raw error to stand alone. `maybe_stop_existing_stack` above
    offers an interactive stop+recreate prompt *before* `up` runs, but only
    developer-mode `setup_all.py` calls it - `python3 run.py` run directly,
    and `setup_all.py` outside developer mode (both fully supported entry
    points), reach `docker compose up` with no such check first. run.py's
    contract is to stay fully non-interactive (see its own docstring), so
    this never prompts - it only prints guidance and lets the caller exit
    with the original failure's return code.

    project_args should scope by project name only (e.g.
    compose_project_args("dev")), not also by the -f file args that picked
    a specific stack: the whole point is to catch containers left running
    under the same project name by a *different* set of compose files
    (e.g. from switching between docker-compose.yaml and
    docker-compose.local.yaml, which share a default project name and
    several service names) - `ps` scoped to one file set's services can
    miss those."""
    running = compose_running_services(project_args)
    print()
    print("--- 'docker compose up' failed ---")
    if running:
        print(
            "An existing Horseless Carriage stack looks like it's already running "
            f"({', '.join(sorted(set(running)))}), which can conflict with the one just started"
        )
        print("(e.g. from switching between the local/Ollama and cloud setups, which share a Compose")
        print("project name and several service names, or a container left over from an interrupted run).")
    else:
        print("This can happen when a leftover/conflicting Horseless Carriage stack is still running")
        print("(e.g. from switching between the local/Ollama and cloud setups, or a container left over")
        print("from an earlier interrupted run) - even if none was detected just now.")
    print(f"Try: docker compose {' '.join(project_args)} down")
    print("See TROUBLESHOOTING.md, section 2 'Docker / container problems', for details.")
