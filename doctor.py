#!/usr/bin/env python3
"""
Doctor script for the Horseless Carriage project.

This script will:
1. Check for Docker and Docker Compose.
2. Check if .env exists and contains essential variables.
3. Check if the STATE_REPO_PATH directory exists, has a 'specs' subdirectory
   with no stray template files (see check_state_repo.py for the fuller,
   heavier version of this check, including state.json validation), and
   materialize each role's default `<Role>-identity.md` if it doesn't
   already exist (additive only - never overwrites an existing file).
4. Check gh CLI authentication, and - given a GITHUB_REPO_URL and either
   GITHUB_TOKEN or a resolvable GitHub App token - live read access to that
   repo's issues and pull requests (see lib_github.py).
5. Check the LLM/LiteLLM proxy configuration (see setup_llm.py) - including a
   live test request if the proxy is already running.
6. If OLLAMA_GPU_ENABLED=true and the ollama container is running, warn
   loudly if it's actually running on CPU (see lib_docker.ollama_gpu_status)
   - a driver/WSL2 misconfiguration otherwise fails silently. If
   OLLAMA_HOST_MODE=true instead (GH issue #93: Ollama running natively on
   the host, e.g. for macOS GPU/Metal support), checks host reachability
   directly instead (see lib_docker.host_ollama_reachable). See also
   print_ollama_gpu_confirmation() below (GH issue #235): a standalone
   version of this same check that run.py calls right after the stack it
   actually started comes up, so a GPU/host-Ollama fallback is surfaced
   proactively even on a plain `python3 run.py` (which gates on check()
   with skip_llm_probe=True, before any container exists to check).

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
import project_registry
import lib_github
import lib_llm_test

# ISSUE-0064: kept independent of agents/scrum_team/tools/test_runners.py
# (the in-container, non-stdlib-only tool of record for this same
# detection) deliberately - this module is stdlib-only by design (see its
# own docstring) and runs on the host before any agent container exists,
# while importing anything under agents/ would pull in google-adk/litellm
# just to check for a manifest file.
_JVM_MANIFESTS = ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "pom.xml")
_UNSUPPORTED_ECOSYSTEM_MANIFESTS = (
    ("go.mod", "Go"),
    ("Cargo.toml", "Rust"),
    ("Gemfile", "Ruby"),
    ("composer.json", "PHP"),
    ("*.csproj", ".NET"),
    ("*.sln", ".NET"),
)
_NEW_ISSUE_URL = "https://github.com/CI-Till-Krempel/Horseless-Carriage/issues/new"


def _detect_unsupported_ecosystem(state_repo_path: Path) -> str | None:
    for pattern, ecosystem in _UNSUPPORTED_ECOSYSTEM_MANIFESTS:
        if "*" in pattern:
            if list(state_repo_path.glob(pattern)):
                return ecosystem
        elif (state_repo_path / pattern).exists():
            return ecosystem
    return None


def _new_issue_url(ecosystem: str, state_repo_path: Path) -> str:
    from urllib.parse import quote
    title = f"Add test-runner support for {ecosystem}"
    body = (
        f"Horseless Carriage detected a {ecosystem} project at `{state_repo_path}` with no "
        "test-runner adapter (see agents/scrum_team/tools/test_runners.py) - the Tested gate "
        "and calculate_kpis can't run or measure this project's real test suite yet."
    )
    return f"{_NEW_ISSUE_URL}?title={quote(title)}&body={quote(body)}"


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
    else:
        # The CLI binary being present doesn't mean the daemon is actually
        # running (Docker Desktop installed but not started is a common
        # real-world state, e.g. right after a reboot) - `docker compose
        # version` below only needs the CLI, not a live daemon, so it can't
        # catch this. Without this check, a setup with Docker Desktop
        # stopped passes doctor cleanly and only fails once run.py actually
        # tries `docker compose up` (GH issue #107) - exactly the "broken
        # setup passes the gate" case doctor exists to prevent.
        try:
            subprocess.run(["docker", "info"], check=True, timeout=10,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            error("Docker CLI found, but the Docker daemon isn't running (or isn't reachable). "
                  "Start Docker Desktop (or the docker service), then re-run doctor.py.")

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

    # ISSUE-0063 follow-up: an installation from before new_project.py kept
    # "horseless-carriage_" on the front of an auto-derived
    # COMPOSE_PROJECT_NAME_PREFIX has one that's just the bare target-repo
    # name (e.g. "heinzelmann") - on a shared machine running several
    # unrelated projects, nothing in `docker ps`/`docker images` then reads
    # as a Horseless Carriage installation at a glance. Only ever a
    # warning with manual migration steps, never auto-fixed here: changing
    # this value changes the Compose project name itself, so the next
    # `docker compose up` would build a brand-new stack rather than
    # recreating the existing one - a choice for the user to make
    # knowingly, not something doctor.py should silently rewrite in .env.
    old_prefix = env.get("COMPOSE_PROJECT_NAME_PREFIX")
    if old_prefix and old_prefix != "horseless-carriage" and not old_prefix.startswith("horseless-carriage_"):
        new_prefix = f"horseless-carriage_{old_prefix}"
        warn(
            f"COMPOSE_PROJECT_NAME_PREFIX='{old_prefix}' predates the 'horseless-carriage_' naming "
            "convention new_project.py now uses, so this installation's containers/images don't read "
            "as a Horseless Carriage installation at a glance. To migrate (for each of dev/eval/test "
            f"you use): (1) edit .env, set COMPOSE_PROJECT_NAME_PREFIX='{new_prefix}'; "
            f"(2) docker compose -p {old_prefix}-dev down; (3) docker compose -p {new_prefix}-dev up -d. "
            "Only LiteLLM's spend-log history and ADK's own eval-run bookkeeping reset - no Scrum-team "
            "data lives in Docker volumes (specs/state/git history are all bind-mounted from the host)."
        )

    # GH issue #310: a nested installation (this repo_root is a
    # horseless-carriage/ submodule of some other project) should be
    # registered in project_registry.py so dashboard.py can list it -
    # whether that happened via new_project.py or by hand (adding the
    # submodule + .env manually is explicitly supported too). Mirrors
    # new_project.py's own _install_if_nested nesting detection exactly
    # (parent directory is itself a git repo).
    parent_dir = repo_root.parent
    if (parent_dir / ".git").exists():
        registered_paths = {entry.get("hc_path") for entry in project_registry.load()}
        if str(repo_root) not in registered_paths:
            warn(
                f"{repo_root} looks like a nested installation (submodule of {parent_dir}) but isn't "
                "registered in the dashboard project registry - python3 dashboard.py won't show it. "
                "Run python3 new_project.py to register it (safe to re-run - never overwrites existing "
                ".env values)."
            )

    # 3. Check GitHub configuration
    if not env.get("GITHUB_REPO_URL"):
        warn("GITHUB_REPO_URL is not set in .env. The agent might not know which repository to use.")

    if env.get("GITHUB_TOKEN"):
        print("GitHub Authentication: Using Personal Access Token.")
    elif lib_github.github_app_fully_configured(env):
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
            # GH issue #(new, follow-up to #288): catch a repointed
            # STATE_REPO_PATH before `docker compose up` silently stops and
            # recreates a container that's still actually serving a
            # DIFFERENT project under this same Compose project name - a
            # real incident: a standalone checkout's STATE_REPO_PATH changed
            # (see setup_llm.py's _setup_state_repo) while an unrelated
            # project's agent was still running under the same (default)
            # project name, and the next `docker compose up` would have
            # destroyed it with no warning at all. Only meaningful for the
            # "dev" stack run.py actually starts - eval/test runs override
            # the mount via INTERNAL_STATE_REPO_PATH, never this value.
            dev_compose_args = lib_docker.compose_file_args(repo_root) + lib_docker.compose_project_args("dev", repo_root)
            running_mount = lib_docker.running_state_repo_mount(dev_compose_args, cwd=repo_root)
            if running_mount is not None:
                running_resolved = Path(lib_docker.normalize_docker_mount_source(running_mount)).resolve()
                configured_resolved = state_repo_path.resolve()
                if running_resolved != configured_resolved:
                    error(
                        f"A Horseless Carriage agent is ALREADY RUNNING under this checkout's Compose "
                        f"project name, but it's serving a DIFFERENT state repo ({running_resolved}) than "
                        f"what .env currently configures ({configured_resolved}). Starting this stack now "
                        "would stop and recreate that container against the new target, silently abandoning "
                        f"whichever project it was actually driving. Stop it first if that's intended "
                        f"(docker compose {' '.join(dev_compose_args)} down), or give each project its own "
                        "COMPOSE_PROJECT_NAME_PREFIX in .env if both are meant to keep running."
                    )

            # ISSUE-0064: surfaced here (at doctor/startup time) rather than
            # only discovered mid-sprint when QA's Tested gate runs the real
            # test suite and gets a confusing result - a real incident burned
            # a QA cycle on exactly that before anyone realized the target
            # project's build tool had no adapter at all, not that something
            # was actually broken.
            unsupported = _detect_unsupported_ecosystem(state_repo_path)
            if unsupported:
                warn(f"The project at STATE_REPO_PATH looks like a {unsupported} project, which Horseless "
                     "Carriage has no test-runner adapter for yet - the Tested gate won't be able to run or "
                     f"measure its real test suite. Request support: {_new_issue_url(unsupported, state_repo_path)}")
            elif (
                any(next(state_repo_path.glob(f"**/{m}"), None) is not None for m in _JVM_MANIFESTS)
                and env.get("INSTALL_JDK", "").lower() != "true"
            ):
                warn("The project at STATE_REPO_PATH looks like a Gradle/Maven (JVM) project, but INSTALL_JDK "
                     "isn't set to 'true' in .env - the agent image has no JDK, so `./gradlew test`/`./mvnw test` "
                     "can't run. Set INSTALL_JDK=true in .env and rebuild the agent image.")

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

                # Migration for state repos that predate the per-role
                # identity.md convention - purely additive (never
                # overwrites an existing file, see
                # ensure_role_identity_defaults's docstring), so this runs
                # unconditionally on every doctor.py invocation rather than
                # only warning like the stray-template check above, the
                # same way the sessions/ auto-create below does.
                identity_results = check_state_repo.ensure_role_identity_defaults(state_repo_path)
                created_roles = sorted(role for role, result in identity_results.items() if result == "created")
                if created_roles:
                    print(f"NOTE: Created default identity.md in the state repository for {len(created_roles)} "
                          f"role(s) not yet customized: {', '.join(created_roles)}.")

    sessions_dir = repo_root / "sessions"
    if not sessions_dir.is_dir():
        print("NOTE: 'sessions' directory not found. Creating it...")
        sessions_dir.mkdir(parents=True, exist_ok=True)

    # 5. Check gh CLI authentication (still useful for local development and
    # setup) - but only when GitHub App auth isn't fully configured. Per
    # PREFLIGHT.md, `gh auth status` is only relevant to the Personal
    # Account auth method; a user who deliberately set up a GitHub App and
    # never ran `gh auth login` shouldn't get a spurious warning that
    # contradicts the checklist (GH issue #243).
    if lib_github.github_app_fully_configured(env):
        print("NOTE: Skipping 'gh auth status' check - GitHub App auth is fully configured.")
    elif shutil.which("gh") is None:
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

    host_ollama_mode = active_provider == "local" and env.get("OLLAMA_HOST_MODE") == "true"

    key_var = lib_llm_test.llm_provider_key_var(active_provider)
    if key_var:
        key_value = env.get(key_var, "")
        if lib_env.is_placeholder(key_value):
            warn(f"{key_var} is not set (or still a placeholder) in .env. Run python3 setup_llm.py to configure it.")
    elif active_provider == "local" and not env.get("OLLAMA_MODEL"):
        where = "your host's Ollama install" if host_ollama_mode else "the ollama container"
        print(f"NOTE: OLLAMA_MODEL is not set in .env - {where} will default to llama3.1:8b.")

    if not skip_llm_probe and host_ollama_mode:
        if lib_docker.host_ollama_reachable():
            print("Host Ollama: reachable at http://localhost:11434.")
        else:
            warn("Host Ollama (GH issue #93 host mode) is not reachable at http://localhost:11434. "
                 "Run `ollama serve` on this machine before starting the stack.")
    elif not skip_llm_probe and active_provider == "local" and env.get("OLLAMA_GPU_ENABLED") == "true":
        # GH issue #169: query under the same project name run.py's dev
        # stack actually starts under, so this sees the right containers.
        compose_args = lib_docker.compose_file_args(repo_root) + lib_docker.compose_project_args("dev", repo_root)
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

        # GH issue #298: a working chat completion above only proves the
        # model id resolves and responds - it says nothing about whether
        # LiteLLM's bundled pricing table actually has a price for it, so
        # check that separately.
        pricing_warnings = lib_llm_test.llm_check_model_pricing(proxy_base_url, env.get("LITELLM_MASTER_KEY", ""))
        for pricing_warning in pricing_warnings:
            warn(pricing_warning)
    else:
        print(f"NOTE: LiteLLM proxy not reachable at {proxy_base_url} (containers not running?).")
        # GH issue #169: match run.py's own dev-stack project name, so a
        # copy-pasted manual start stays recognizable as the same stack.
        project_flag = " ".join(lib_docker.compose_project_args("dev", repo_root))
        if host_ollama_mode:
            print(f"  Start it with: docker compose -f docker-compose.local.yaml -f docker-compose.local-hostollama.yaml {project_flag} up -d db litellm")
        elif active_provider == "local":
            print(f"  Start it with: docker compose -f docker-compose.local.yaml {project_flag} up -d db litellm ollama")
        else:
            print(f"  Start it with: docker compose {project_flag} up -d db litellm")

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


def print_ollama_gpu_confirmation(repo_root: Path) -> None:
    """Standalone, informational-only companion to check()'s section 6 GPU/
    host-Ollama confirmation (GH issue #235) - for a caller like run.py that
    wants just this one live check right after the local/Ollama stack has
    actually come up, without re-running check()'s whole punch list (which
    would reprint every other item, and re-pay the .env/GitHub/LLM-proxy
    checks all over again).

    Mirrors check()'s own detection logic exactly, but prints nothing at all
    (rather than a warning) if a local/GPU Ollama setup isn't actually in
    use, or if the GPU/host-Ollama status can't be determined yet (ollama
    still starting up, hasn't logged its "inference compute" line yet) -
    this is meant to be called once, right after startup, not polled, so a
    false negative here shouldn't read as a real problem. Never raises,
    never gates anything - see lib_docker.ollama_gpu_status's own
    best-effort contract."""
    repo_root = Path(repo_root)
    env = lib_env.load_env_file(repo_root / ".env")
    active_provider = lib_llm_test.llm_active_provider(lib_llm_test.llm_active_config_path(repo_root))
    if active_provider != "local":
        return

    if env.get("OLLAMA_HOST_MODE") == "true":
        if lib_docker.host_ollama_reachable():
            print("✓ GPU acceleration active (host Ollama reachable at http://localhost:11434).")
        else:
            print("⚠ Host Ollama (GH issue #93 host mode) is not reachable at http://localhost:11434 - "
                  "run `ollama serve` on this machine, then re-check with: python3 doctor.py")
        return

    if env.get("OLLAMA_GPU_ENABLED") != "true":
        return

    compose_args = lib_docker.compose_file_args(repo_root) + lib_docker.compose_project_args("dev", repo_root)
    if "ollama" not in lib_docker.compose_running_services(compose_args):
        return
    gpu_status = lib_docker.ollama_gpu_status(compose_args)
    if gpu_status == "cpu":
        print("⚠ GPU requested but Ollama is running on CPU - see: docker compose "
              + " ".join(compose_args) + " logs ollama")
    elif gpu_status == "cuda":
        print("✓ GPU acceleration active (Ollama reports library=cuda).")
    # else: not determinable yet (best-effort) - stay silent rather than guess.


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
