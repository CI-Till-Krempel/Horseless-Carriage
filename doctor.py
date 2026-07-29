#!/usr/bin/env python3
"""
Doctor script for the Horseless Carriage project.

This script will:
1. Check for Docker and Docker Compose.
2. Check if .env exists and contains essential variables.
3. Check if the STATE_REPO_PATH directory exists, has a 'specs' subdirectory
   with no stray template files (see check_state_repo.py for the fuller,
   heavier version of this check, including state.json validation).
4. Check gh CLI authentication, and - given a GITHUB_REPO_URL and either
   GITHUB_TOKEN or a resolvable GitHub App token - live read access to that
   repo's issues and pull requests (see lib_github.py).
5. Check the LLM/LiteLLM proxy configuration (see setup_llm.py) - including a
   live test request if the proxy is already running.
6. If OLLAMA_GPU_ENABLED=true and the ollama container is running, warn
   loudly if it's actually running on CPU (see lib_docker.ollama_gpu_status)
   - a driver/WSL2 misconfiguration otherwise fails silently.

Every problem found is collected into a punch list of ActionableItems (see
check()) instead of stopping at the first one - a user fixing configuration
by hand sees everything that needs attention in one pass, not
fix-one/rerun/discover-the-next-one. This also makes doctor.py usable as a
pre-flight gate by other scripts (run.py, setup_all.py): call check() directly
and inspect the result's .has_errors / .items, rather than parsing printed
text or relying on an exit code alone.

Stdlib-only, works identically on macOS/Linux/Windows.
"""

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import check_state_repo
import lib_docker
import lib_env
import lib_github
import lib_llm_test


@dataclass
class ActionableItem:
    """One thing found wrong with the current setup. `severity` is
    "error" (blocks running the agent) or "warning" (won't block it, but
    worth fixing)."""
    severity: str
    message: str


@dataclass
class DoctorResult:
    """The full punch list from one check() run."""
    items: list = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.items)

    @property
    def ok(self) -> bool:
        """True if nothing at all was found wrong (no errors, no warnings)."""
        return not self.items

    def errors(self) -> list:
        return [i for i in self.items if i.severity == "error"]

    def warnings(self) -> list:
        return [i for i in self.items if i.severity == "warning"]

    def print_summary(self) -> None:
        """Prints the consolidated punch list - call after check() if you
        want the summary on its own (check() already prints it once at the
        end of its own run, so this is for a caller re-displaying a
        previously computed result, e.g. setup_all.py re-showing it after a
        fix-and-retry loop)."""
        if not self.items:
            print("No actionable items - setup looks good.")
            return
        print("--- Actionable Items ---")
        for item in self.items:
            print(f"[{item.severity.upper()}] {item.message}")


def check(repo_root: Path, proxy_base_url: str = "http://localhost:4000", skip_llm_probe: bool = False) -> DoctorResult:
    """Runs every check against repo_root (no chdir, so this is safe to call
    from tests against a tmp_path fixture, or from another script like
    run.py/setup_all.py). Never stops at the first problem found - every
    ActionableItem is collected so the caller gets the full punch list in
    one pass, and a check further down doesn't get skipped just because an
    earlier one failed (e.g. a missing STATE_REPO_PATH doesn't prevent
    checking GitHub auth or the LLM proxy).

    skip_llm_probe=True skips the live "is the proxy already reachable"
    network check (section 6 below still reports the active provider/key
    configuration either way, which is cheap/local) - for a caller like
    run.py's pre-flight gate, called before the containers are even
    started, where that check could only ever report "not reachable" and
    would otherwise cost several real seconds for no benefit."""
    repo_root = Path(repo_root)
    items = []

    def error(msg: str) -> None:
        print(f"ERROR: {msg}")
        items.append(ActionableItem("error", msg))

    def warn(msg: str) -> None:
        print(f"WARNING: {msg}")
        items.append(ActionableItem("warning", msg))

    print("--- Running Horseless Carriage Doctor ---")

    # 1. Check for Docker and Docker Compose
    docker_ok = shutil.which("docker") is not None
    if not docker_ok:
        error("'docker' command not found. Please install Docker.")

    compose_ok = False
    if shutil.which("docker-compose") is not None:
        compose_ok = True
    elif docker_ok:
        try:
            subprocess.run(["docker", "compose", "version"], check=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            compose_ok = True
        except Exception:
            compose_ok = False
    if not compose_ok:
        error("'docker-compose' or 'docker compose' command not found. Please install Docker Compose.")

    # 2. Check .env file
    env_path = repo_root / ".env"
    if not env_path.is_file():
        error(".env file not found. Please copy .env.example to .env and fill in the values.")

    env = lib_env.load_env_file(env_path)

    if not env.get("LITELLM_MASTER_KEY"):
        error("LITELLM_MASTER_KEY is not set in .env. Please set it.")

    if not env.get("STATE_REPO_PATH"):
        error("STATE_REPO_PATH is not set in .env. Please set it.")

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

    if not skip_llm_probe and env.get("GITHUB_REPO_URL") and (env.get("GITHUB_TOKEN") or env.get("GITHUB_APP_ID")):
        owner_repo = lib_github.parse_owner_repo(env["GITHUB_REPO_URL"])
        if owner_repo is None:
            warn(f"GITHUB_REPO_URL doesn't look like a github.com repo URL: {env['GITHUB_REPO_URL']!r}")
        else:
            owner, repo = owner_repo
            token, token_source = lib_github.resolve_token(env)
            if token is None:
                if token_source == "app":
                    warn("Could not mint a GitHub App installation token to verify repo access - check "
                         "GITHUB_APP_ID/GITHUB_APP_PRIVATE_KEY/GITHUB_APP_INSTALLATION_ID, and that "
                         "PyJWT and requests are installed.")
            else:
                access_ok, access_detail = lib_github.check_repo_access(owner, repo, token)
                if access_ok:
                    print(f"GitHub access: {access_detail}")
                else:
                    warn(f"GitHub access: {access_detail}")

    # 4. Check if the directories exist
    state_repo_path_str = env.get("STATE_REPO_PATH")
    if state_repo_path_str:
        state_repo_path = Path(state_repo_path_str).expanduser()
        if not state_repo_path.is_dir():
            error(f"The directory specified by STATE_REPO_PATH does not exist: {state_repo_path}")
            print("Please create this directory before running the agent.")
        else:
            specs_dir = state_repo_path / "specs"
            if not specs_dir.is_dir():
                warn(f"The state repository at {state_repo_path} has no 'specs' directory yet - "
                     "run python3 check_state_repo.py for a fuller check.")
            else:
                stray_templates = check_state_repo.stray_template_files(specs_dir)
                if stray_templates:
                    warn(f"State repository has {len(stray_templates)} stray TEMPLATE-*.md file(s) "
                         "in 'specs/' that belong only in this project's spec-templates/ directory - "
                         "run python3 check_state_repo.py for details.")

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

    if not skip_llm_probe and active_provider == "local" and env.get("OLLAMA_GPU_ENABLED") == "true":
        compose_args = lib_docker.compose_file_args(repo_root)
        if "ollama" in lib_docker.compose_running_services(compose_args):
            gpu_status = lib_docker.ollama_gpu_status(compose_args)
            if gpu_status == "cpu":
                print("!" * 70)
                warn("OLLAMA_GPU_ENABLED=true, but Ollama reports running on CPU (library=cpu) - "
                     "the GPU is NOT actually being used. Check the driver/WSL2 prerequisites in "
                     "docs/SETUP.md's \"GPU Support\" section, then verify with: "
                     "docker compose " + " ".join(compose_args) + " exec ollama nvidia-smi")
                print("!" * 70)
            elif gpu_status == "cuda":
                print("GPU acceleration confirmed: Ollama reports library=cuda.")

    if skip_llm_probe:
        print("(Skipping live proxy reachability check - not needed here.)")
    elif lib_llm_test.llm_wait_for_proxy(proxy_base_url, 5):
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

    result = DoctorResult(items=items)

    print()
    result.print_summary()

    print()
    print("--- Doctor Check Complete ---")
    if result.has_errors:
        print("Fix the ERROR items above before running the agent.")
    elif items:
        print("Setup looks functional, but see the WARNING items above.")
        print("You can now run the agent with: python3 run.py")
    else:
        print("Setup looks good. You can now run the agent with: python3 run.py")

    return result


def run(repo_root: Path, proxy_base_url: str = "http://localhost:4000") -> int:
    """Thin int-returning wrapper around check(), kept for backward
    compatibility (main() below, and anything that only cares about the
    exit code) - see check() for the full structured result (the punch
    list of ActionableItems) that run.py/setup_all.py use directly."""
    return 1 if check(repo_root, proxy_base_url).has_errors else 0


def main() -> None:
    sys.exit(run(Path(__file__).resolve().parent))


if __name__ == "__main__":
    main()
