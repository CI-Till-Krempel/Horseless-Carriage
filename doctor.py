#!/usr/bin/env python3
"""
Doctor script for the Horseless Carriage project.

This script will:
1. Check for Docker and Docker Compose.
2. Check if .env exists and contains essential variables.
3. Check if the STATE_REPO_PATH directory exists.
4. Check gh CLI authentication.
5. Check the LLM/LiteLLM proxy configuration (see setup_llm.py) - including a
   live test request if the proxy is already running.

Stdlib-only, works identically on macOS/Linux/Windows.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import lib_env
import lib_llm_test


def error(msg: str) -> None:
    print(f"ERROR: {msg}")


def warn(msg: str) -> None:
    print(f"WARNING: {msg}")


def run(repo_root: Path, proxy_base_url: str = "http://localhost:4000") -> int:
    """Runs every check against repo_root (no chdir, so this is safe to call
    from tests against a tmp_path fixture). Returns a process exit code."""
    repo_root = Path(repo_root)

    print("--- Running Horseless Carriage Doctor ---")

    # 1. Check for Docker and Docker Compose
    if shutil.which("docker") is None:
        error("'docker' command not found. Please install Docker.")
        return 1

    compose_ok = False
    if shutil.which("docker-compose") is not None:
        compose_ok = True
    else:
        try:
            subprocess.run(["docker", "compose", "version"], check=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            compose_ok = True
        except Exception:
            compose_ok = False
    if not compose_ok:
        error("'docker-compose' or 'docker compose' command not found. Please install Docker Compose.")
        return 1

    # 2. Check .env file
    env_path = repo_root / ".env"
    if not env_path.is_file():
        error(".env file not found. Please copy .env.example to .env and fill in the values.")
        return 1

    env = lib_env.load_env_file(env_path)

    if not env.get("LITELLM_MASTER_KEY"):
        error("LITELLM_MASTER_KEY is not set in .env. Please set it.")
        return 1

    if not env.get("STATE_REPO_PATH"):
        error("STATE_REPO_PATH is not set in .env. Please set it.")
        return 1

    if not env.get("GIT_USER_NAME"):
        warn("GIT_USER_NAME is not set in .env. Defaulting to 'DevTeam'.")

    if not env.get("GIT_USER_EMAIL"):
        warn("GIT_USER_EMAIL is not set in .env. Git commits may fail if not configured.")

    if not env.get("LOG_LEVEL"):
        print("NOTE: LOG_LEVEL is not set in .env. Defaulting to 'info'.")

    # 3. Check GitHub configuration
    if not env.get("GITHUB_REPO_URL"):
        warn("GITHUB_REPO_URL is not set in .env. The agent might not know which repository to use.")

    if env.get("GITHUB_TOKEN"):
        print("GitHub Authentication: Using Personal Access Token.")
    elif env.get("GITHUB_APP_ID") and env.get("GITHUB_APP_PRIVATE_KEY") and env.get("GITHUB_APP_INSTALLATION_ID"):
        print("GitHub Authentication: Using GitHub App.")
    else:
        warn("No GitHub authentication method fully configured in .env.")
        print("Please set either GITHUB_TOKEN or (GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY, and GITHUB_APP_INSTALLATION_ID).")

    # 4. Check if the directories exist
    state_repo_path = Path(env["STATE_REPO_PATH"]).expanduser()
    if not state_repo_path.is_dir():
        error(f"The directory specified by STATE_REPO_PATH does not exist: {state_repo_path}")
        print("Please create this directory before running the agent.")
        return 1

    sessions_dir = repo_root / "sessions"
    if not sessions_dir.is_dir():
        print("NOTE: 'sessions' directory not found. Creating it...")
        sessions_dir.mkdir(parents=True, exist_ok=True)

    # 5. Check gh CLI authentication (still useful for local development and setup)
    if shutil.which("gh") is None:
        warn("'gh' command not found. This may be needed for initial GitHub setup.")
    else:
        try:
            subprocess.run(["gh", "auth", "status"], check=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            warn("gh CLI is not authenticated. Please run 'gh auth login' if you need to interact with GitHub locally.")

    # 6. Check the LLM/LiteLLM proxy configuration - see setup_llm.py
    print()
    print("--- LLM Configuration ---")

    active_config_path = lib_llm_test.llm_active_config_path(repo_root)
    active_provider = lib_llm_test.llm_active_provider(active_config_path)
    print(f"Active provider ({active_config_path.relative_to(repo_root)}): {active_provider}")

    key_var = lib_llm_test.llm_provider_key_var(active_provider)
    if key_var:
        key_value = env.get(key_var, "")
        if lib_env.is_placeholder(key_value):
            warn(f"{key_var} is not set (or still a placeholder) in .env. Run python3 setup_llm.py to configure it.")
    elif active_provider == "local" and not env.get("OLLAMA_MODEL"):
        print("NOTE: OLLAMA_MODEL is not set in .env - the ollama container will default to llama3.1:8b.")

    if lib_llm_test.llm_wait_for_proxy(proxy_base_url, 5):
        print(f"LiteLLM proxy: reachable at {proxy_base_url}")
        print("Sending a live test request to scrum-po (this uses a real, minimal request against your configured model)...")
        ok, detail = lib_llm_test.llm_test_alias(proxy_base_url, env.get("LITELLM_MASTER_KEY", ""), "scrum-po", 30)
        if ok:
            print(f"LLM connectivity: OK - {detail}")
        else:
            warn(f"LLM connectivity test failed - {detail}")
    else:
        print(f"NOTE: LiteLLM proxy not reachable at {proxy_base_url} (containers not running?).")
        if active_provider == "local":
            print("  Start it with: docker compose -f docker-compose.local.yaml up -d db litellm ollama")
        else:
            print("  Start it with: docker compose up -d db litellm")

    print()
    print("--- Doctor Check Complete ---")
    print("Setup looks good. You can now run the agent with: python3 run.py")
    return 0


def main() -> None:
    sys.exit(run(Path(__file__).resolve().parent))


if __name__ == "__main__":
    main()
