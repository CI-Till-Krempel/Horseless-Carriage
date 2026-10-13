#!/usr/bin/env python3
"""
GH issue #288: start using Horseless Carriage in a project of your own,
without a single, unshareable checkout - one checkout only ever supports
one active project (STATE_REPO_PATH/COMPOSE_PROJECT_NAME_PREFIX/host ports
would all collide the moment a second project tried to reuse it).

Two ways to run this:

  1. You've already added this repo as a git submodule of your target
     project (`git submodule add <this-repo-url> horseless-carriage`, run
     from inside your project) and are running this script from inside
     that submodule directory:

         cd your-project/horseless-carriage
         python3 new_project.py

  2. You want this script to add the submodule for you too:

         python3 new_project.py --target-repo /path/to/your-project

     If that path doesn't exist yet, it's created and `git init`'d (a
     genuinely brand-new project). If it exists but isn't a git repo yet,
     you'll be asked before `git init` runs in place. Either way, this HC
     checkout is then added as a `horseless-carriage/` submodule of it -
     requires this checkout to itself be a real git clone with an `origin`
     remote (a release tag/zip download has nothing to point the submodule
     at - clone this repo with git instead).

Either way, once "installed" (running from inside a target repo, one level
under its own git root), this script pre-fills three things a second/
subsequent project would otherwise collide on if just copy-pasting one
`.env` between checkouts:
  - STATE_REPO_PATH -> defaults to the target repo itself (instead of
    setup_llm.py's traditional sibling-directory default), since the whole
    point of installing HC *into* a project is that the project itself is
    what the team's specs/reports get written into.
  - COMPOSE_PROJECT_NAME_PREFIX -> derived from the target repo's own
    directory name, so this project's containers/images never collide with
    another HC installation's (GH issue #169's original fix only
    disambiguated *contexts* - dev/eval/test - within one checkout; this
    disambiguates *installations* from each other).
  - LITELLM_HOST_PORT/AGENT_WEB_HOST_PORT -> auto-picked free ports if the
    defaults (4000/8000) are already bound on this machine - e.g. another
    HC-powered project's stack is already running - so multiple projects
    can run concurrently instead of the second one just failing to bind.

None of this ever overwrites an already-configured value in `.env` (a
re-run is always safe), and none of it applies at all if this checkout
isn't nested inside another repo - running this script against a
traditional standalone checkout is a harmless no-op that just hands off to
setup_all.py's normal guided flow.

GH issue #310: a nested (installed-into-a-project) run also registers the
project in project_registry.py's shared list (~/.horseless-carriage/
projects.json), so `python3 dashboard.py` can show every project installed
on this machine, not just the one you happen to be looking at right now.
"""
import argparse
import os
import shutil
import socket
import subprocess
from pathlib import Path

import lib_env
import project_registry
import setup_all
import setup_llm

info = setup_llm.info
warn = setup_llm.warn
die = setup_llm.die


def _sanitize_compose_project_prefix(name: str) -> str:
    """Compose project names must match ^[a-z0-9][a-z0-9_-]*$ - derive one
    from an arbitrary repo directory name (which might contain spaces,
    dots, uppercase, ...) instead of failing outright on the first
    project whose directory name isn't already a valid one."""
    sanitized = "".join(c.lower() if c.isalnum() else "-" for c in name)
    while "--" in sanitized:
        sanitized = sanitized.replace("--", "-")
    sanitized = sanitized.strip("-")
    if not sanitized or not sanitized[0].isalnum():
        sanitized = f"hc-{sanitized}" if sanitized else "hc-project"
    return sanitized


def _is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _find_free_port(preferred: int, max_attempts: int = 50) -> int:
    """preferred if free, else the first free port scanning upward - so a
    second/third concurrent installation gets its own port instead of
    failing to bind at `docker compose up` time with no clear reason why."""
    for port in range(preferred, preferred + max_attempts):
        if _is_port_free(port):
            return port
    die(f"Could not find a free port near {preferred} after {max_attempts} attempts.")


def _git_remote_url(repo_dir: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _add_as_submodule(this_checkout: Path, target_repo: Path) -> Path:
    """Adds this_checkout as a `horseless-carriage/` git submodule of
    target_repo (creating/git-init'ing target_repo first if needed) and
    returns the path to the newly added submodule directory - the new
    repo_root every following step in this script operates on."""
    remote_url = _git_remote_url(this_checkout)
    if not remote_url:
        die(
            f"{this_checkout} has no 'origin' git remote to add as a submodule - "
            "clone Horseless Carriage with git (not a release zip/tarball) and retry, "
            "or add the submodule yourself: git submodule add <repo-url> horseless-carriage"
        )

    submodule_path = target_repo / "horseless-carriage"
    if submodule_path.is_dir():
        info(f"{submodule_path} already exists - assuming it's already the submodule, skipping `submodule add`.")
        return submodule_path

    target_repo.mkdir(parents=True, exist_ok=True)
    if not (target_repo / ".git").exists():
        if not setup_all.confirm(f"{target_repo} is not a git repository yet - run `git init` there?", default_yes=True):
            die(f"Cannot add a submodule to a non-git directory. Run `git init {target_repo}` yourself and retry.")
        subprocess.run(["git", "init", str(target_repo)], check=True)

    info(f"Adding Horseless Carriage as a submodule: {target_repo} $ git submodule add {remote_url} horseless-carriage")
    result = subprocess.run(
        ["git", "-C", str(target_repo), "submodule", "add", remote_url, "horseless-carriage"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        die(f"`git submodule add` failed:\n{result.stdout}\n{result.stderr}")

    return submodule_path


def _configure_for_target_project(repo_root: Path, target_repo: Path) -> None:
    """Pre-fills .env with the three per-installation values described in
    this module's own docstring - only ever filling in a value that isn't
    already set, so re-running this script (e.g. after `git pull`-ing a
    newer Horseless Carriage into an existing submodule) never clobbers a
    choice already made for this project."""
    env_path = repo_root / ".env"
    if not env_path.is_file():
        example_path = repo_root / ".env.example"
        if example_path.is_file():
            shutil.copy(example_path, env_path)
            info("Created .env from .env.example.")

    if not lib_env.read_env_var(env_path, "STATE_REPO_PATH"):
        lib_env.update_env_var(env_path, "STATE_REPO_PATH", str(target_repo))
        info(f"STATE_REPO_PATH defaulted to {target_repo} (the target project itself).")

    if not lib_env.read_env_var(env_path, "COMPOSE_PROJECT_NAME_PREFIX"):
        prefix = _sanitize_compose_project_prefix(target_repo.name)
        lib_env.update_env_var(env_path, "COMPOSE_PROJECT_NAME_PREFIX", prefix)
        info(f"COMPOSE_PROJECT_NAME_PREFIX set to '{prefix}' - keeps this project's containers/images "
             "from colliding with another Horseless Carriage installation's.")

    for var, default_port in (("LITELLM_HOST_PORT", 4000), ("AGENT_WEB_HOST_PORT", 8000)):
        if lib_env.read_env_var(env_path, var):
            continue
        if _is_port_free(default_port):
            continue
        free_port = _find_free_port(default_port + 1)
        lib_env.update_env_var(env_path, var, str(free_port))
        info(f"Port {default_port} is already in use (likely another Horseless Carriage installation) - "
             f"{var} set to {free_port} instead.")


def _install_if_nested(repo_root: Path) -> None:
    """If repo_root is nested one level under another repo (installed as
    that project's submodule - whether by this script or by hand), applies
    the per-project .env defaults and registers it in project_registry.py
    so dashboard.py (GH issue #310) can list it. A no-op for a traditional
    standalone checkout."""
    parent = repo_root.parent
    if not (parent / ".git").exists():
        info(f"{repo_root} is a standalone checkout (not nested inside another repo) - "
             "proceeding with setup_all.py's normal defaults.")
        return

    info(f"Detected {repo_root} is installed inside {parent} - configuring per-project defaults for it.")
    _configure_for_target_project(repo_root, parent)
    # GH issue #310: dashboard.py reads this registry to list every
    # installed project - registering here (not a separate manual step) is
    # what connects the two issues, per #310's own decision comment ("#288
    # owning add a project, this issue owning list/observe registered
    # projects").
    project_registry.register(parent.name, repo_root, parent)
    info(f"Registered '{parent.name}' - see it with: python3 dashboard.py")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--target-repo", type=str, default=None,
        help="Path to the project to install Horseless Carriage into - added as a "
             "horseless-carriage/ git submodule (created/git-init'd first if it doesn't exist yet). "
             "Omit if you've already added the submodule yourself and are running this from inside it.",
    )
    args = parser.parse_args()

    this_checkout = Path(__file__).resolve().parent

    if args.target_repo:
        repo_root = _add_as_submodule(this_checkout, Path(args.target_repo).expanduser().resolve())
    else:
        repo_root = this_checkout

    _install_if_nested(repo_root)

    print()
    os.chdir(repo_root)
    setup_all.main()


if __name__ == "__main__":
    main()
