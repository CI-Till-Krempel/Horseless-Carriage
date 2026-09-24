#!/usr/bin/env python3
"""
Run script for the Horseless Carriage project.

This script will:
1. Use doctor.py as a gatekeeper: refuse to start if the configuration has
   any blocking problem (missing .env, no state repo, etc.) - see
   doctor.check().
2. Build and run the agent container with session management and logging.
3. Wait for the dashboards to come up and open them in your default browser.

Fully non-interactive: it never prompts. The one prompt this flow used to
ask here (stop + recreate a leftover running stack before starting a fresh
one) now lives in setup_all.py's offer_to_start, asked only in developer
mode, before it hands off to this script - see lib_docker
.maybe_stop_existing_stack. Every other entry point (this script run
directly, and setup_all.py outside developer mode - both fully supported)
reaches `docker compose up` below with no such prompt beforehand; if it
fails, this script prints a non-interactive hint pointing at the likely
cause and TROUBLESHOOTING.md instead of just leaving Docker's raw error to
stand alone - see lib_docker.print_stack_conflict_hint (GH issue #232).

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
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import banner
import doctor
import lib_docker
import lib_env
import lib_llm_test
import rebuild_images

LITELLM_DASHBOARD_URL = "http://localhost:4000/ui"
ADK_WEB_URL = "http://localhost:8000"

# GH issue #263: link used to prefill a "report an experimental-feature
# issue" URL from the startup warning banner below.
GITHUB_NEW_ISSUE_URL = "https://github.com/CI-Till-Krempel/Horseless-Carriage/issues/new"


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


def _active_model_name(active_config_path: Path, active_provider: str, env: dict) -> str:
    """Best-effort, non-secret "which model" summary for the warning banner
    below: the OLLAMA_MODEL tag for a Local/Ollama setup (never a key), or
    the `model:` value (e.g. "anthropic/claude-sonnet-5") from whichever
    litellm-config file lib_llm_test.llm_active_config_path() picked for a
    cloud provider. Falls back to just the provider name if the file is
    missing or unreadable - never touches API keys/tokens."""
    if active_provider == "local":
        return env.get("OLLAMA_MODEL") or "llama3.1:8b (default)"
    if not active_config_path.is_file():
        return active_provider
    text = active_config_path.read_text(encoding="utf-8")
    m = re.search(r"^\s*model:\s*(\S+)", text, re.MULTILINE)
    return m.group(1) if m else active_provider


def detect_experimental_features(repo_root: Path, mode: str, daemon: bool) -> list:
    """Which of the four experimental features from GH issue #251 (Terminal
    UI, daemon mode, Local AI/Ollama, a non-"Product" Interaction Level)
    apply to this run's configuration. Empty list if none do."""
    repo_root = Path(repo_root)
    env = lib_env.load_env_file(repo_root / ".env")
    active_config_path = lib_llm_test.llm_active_config_path(repo_root)
    active_provider = lib_llm_test.llm_active_provider(active_config_path)

    features = []
    if mode == "cli":
        features.append("Terminal UI (`run.py cli`)")
    if daemon:
        features.append("Daemon mode (`run.py daemon`)")
    if active_provider == "local":
        features.append("Local AI / Ollama")
    interaction_level = env.get("INTERACTION_LEVEL") or "Product"
    if interaction_level != "Product":
        features.append(f"Interaction Level: {interaction_level} (non-Product)")
    return features


def _safe_os_description() -> str:
    """platform.platform() shells out to `uname -p` for the processor field
    and can raise (e.g. AttributeError on some CI/container hosts where the
    subprocess call succeeds but returns no usable output) - never let an OS
    description failure crash the run."""
    try:
        return platform.platform()
    except Exception:
        try:
            return f"{platform.system()} {platform.release()}".strip()
        except Exception:
            return "unknown"


def build_experimental_issue_url(features: list, interaction_level: str, provider: str, model_name: str) -> str:
    """GitHub's "new issue" URL, prefilled via its documented ?title=&body=
    query params (GH issue #263) with only non-secret, redacted config: OS,
    this tool's VERSION, the Interaction Level, which experimental
    feature(s) are active, and the provider/model *name* - never an API
    key, token, or any other secret-shaped .env value (those are never read
    here in the first place)."""
    title = "Experimental feature: report an issue"
    body = "\n".join([
        "<!-- Describe the problem you ran into below. -->",
        "",
        "",
        "**Configuration (auto-filled, non-secret):**",
        f"- OS: {_safe_os_description()}",
        f"- Horseless Carriage version: {banner.version()}",
        f"- Interaction Level: {interaction_level}",
        f"- Experimental feature(s) active: {', '.join(features)}",
        f"- Provider / model: {provider} / {model_name}",
    ])
    query = urllib.parse.urlencode({"title": title, "body": body})
    return f"{GITHUB_NEW_ISSUE_URL}?{query}"


def print_experimental_warning(repo_root: Path, mode: str, daemon: bool) -> None:
    """Prints a short startup warning banner (GH issue #263) if this run's
    configuration uses one or more of the experimental features flagged by
    GH issue #251 - a doc marker alone doesn't reach a user who's already
    past onboarding. Silent (no-op) if none apply."""
    repo_root = Path(repo_root)
    features = detect_experimental_features(repo_root, mode, daemon)
    if not features:
        return

    env = lib_env.load_env_file(repo_root / ".env")
    active_config_path = lib_llm_test.llm_active_config_path(repo_root)
    active_provider = lib_llm_test.llm_active_provider(active_config_path)
    interaction_level = env.get("INTERACTION_LEVEL") or "Product"
    model_name = _active_model_name(active_config_path, active_provider, env)
    issue_url = build_experimental_issue_url(features, interaction_level, active_provider, model_name)

    print("=" * 56)
    print("WARNING: this run uses experimental feature(s), not yet")
    print("thoroughly tested:")
    for feature in features:
        print(f"  - {feature}")
    print("Hit a problem? Please report it (link pre-filled with your")
    print("non-secret config - OS, version, interaction level, model):")
    print(f"  {issue_url}")
    print("=" * 56)
    print()


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
        # GH issue #235: litellm depends_on ollama, so by the time litellm's
        # own health check passes, a dockerized ollama container (if this is
        # a GPU/local-Ollama setup) has had a real chance to log whether it
        # actually landed on the GPU or silently fell back to CPU - surface
        # that now instead of requiring a separate `python3 doctor.py` run.
        doctor.print_ollama_gpu_confirmation(Path("."))
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
    print_experimental_warning(Path("."), mode, daemon)

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
        thread = threading.Thread(target=open_dashboards, args=(mode,), daemon=True)
        thread.start()

        if daemon:
            result = subprocess.run(["docker", "compose", *full_compose_args, "up", "-d", "--build", "agent"], env=proc_env)
            if result.returncode != 0:
                # GH issue #232: neither this direct `run.py` entry point nor
                # non-dev setup_all.py (which hands off here) offers the
                # interactive leftover-stack prompt that developer-mode
                # setup_all.py does before `up` - print a non-interactive
                # pointer at the likely cause instead of just the raw
                # Docker error.
                lib_docker.print_stack_conflict_hint(lib_docker.compose_project_args("dev"))
                sys.exit(result.returncode)
            thread.join()
            print("Agent container started in daemon mode.")
            logs_cmd = " ".join(["docker", "compose", *full_compose_args, "logs", "-f", "agent"])
            print(f"To view logs, run: {logs_cmd}")
            print("Note: if you reconfigure the provider after this, the -f flags above may be "
                  "stale - run `docker compose ps` to find the running stack.")
        else:
            print("Running ADK web frontend in foreground. Press Ctrl+C to stop.")
            result = subprocess.run(["docker", "compose", *full_compose_args, "up", "--build", "agent"], env=proc_env)
            if result.returncode not in (0, 130):
                # 130 (SIGINT) is the normal Ctrl+C stop this section's own
                # message invites - not a conflict. See the daemon branch
                # above and GH issue #232 for the non-zero case.
                lib_docker.print_stack_conflict_hint(lib_docker.compose_project_args("dev"))
            sys.exit(result.returncode)


if __name__ == "__main__":
    main()
