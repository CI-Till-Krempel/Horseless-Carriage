#!/usr/bin/env python3
"""
Orchestrated, guided setup for Horseless Carriage: runs every setup step in
one go, in the right order, gated by doctor.py before offering to actually
start the agent. Each step is also a fully standalone script you can run on
its own (setup_llm.py, setup_project.py, doctor.py, run.py) - this just
chains them for a first-time/new-machine setup so you don't have to
remember the order or run each one by hand.

Steps:
  0. Developer mode? - asked FIRST, before any container work happens at
     all: dev mode forces a fresh image rebuild (see rebuild_images.py),
     and setup_llm.py's own Local/Ollama live test (step 1) already starts
     a container (ollama) that dev mode would rebuild - asking this only
     at the very end (as the offer-to-start step used to) meant that live
     test validated a stale image a later rebuild silently replaced
     anyway, wasting a real model pull and giving a misleading "it works"
     signal for an image about to be discarded.
  1. setup_llm.py        - provider/model/GPU/interaction-level/budget config,
                           state repository setup, git identity. Prefills
                           whatever's already configured on a re-run. Told
                           about developer mode from step 0, so its own
                           Local/Ollama live test rebuilds ollama first.
  2. check_state_repo.py - verifies the state repository setup_llm.py just
                           created/cloned is actually in the shape the tools
                           expect (specs/ directory, no stray templates, a
                           valid state.json if one already exists) - this
                           used to be a separate step nothing else ever ran
                           for you (GH issue #60).
  3. setup_project.py    - Docker/GitHub CLI checks, .env skeleton, brings up
                           db + litellm(+ollama) (using whichever compose
                           file the configured provider actually needs -
                           see setup_project.py's own docstring).
  4. GitHub auth         - (GH issue #565 / SPEC-ISSUE-0100) skipped if
                           .env already has GITHUB_TOKEN or all three
                           GITHUB_APP_* vars; otherwise lets you pick a
                           GitHub App (setup_github_app.py's own flow) or
                           a Personal Access Token (entered hidden, written
                           only to the git-ignored .env). The agent can't
                           touch the target repo without one, but doctor.py
                           only warns about it - so this used to fall
                           through to "Ready to start the agent" unnoticed.
                           Without a TTY (or with --skip-github-auth) it
                           never prompts: it prints how to configure auth
                           and carries on, and step 6 warns loudly.
  5. doctor.py           - gate: shows the full punch list of anything left
                           to fix, and loops (fix -> retry) until there are
                           no more ERROR-level items, before proceeding.
  6. Offers to start the agent now via run.py, in whichever mode you want -
     developer mode itself was already decided in step 0. In developer
     mode, also offers to stop + recreate a leftover running stack first
     (run.py itself never prompts - see its own docstring) - moved here
     rather than asked unconditionally, since a fresh dev-mode rebuild is
     the case most likely to collide with a stack still running under the
     old images.

Usage:
  python3 setup_all.py        Interactive, guided walkthrough of all of the above.
  python3 setup_all.py --dev  Same, but defaults step 0's developer-mode
                              question to yes instead of asking cold.
  python3 setup_all.py --skip-github-auth
                              Don't prompt for GitHub auth in step 4 (e.g.
                              you'll fill in .env by hand) - still warned
                              about before step 6 if it's missing.
"""

import getpass
import sys
from pathlib import Path

import banner
import check_state_repo
import doctor
import lib_docker
import lib_env
import lib_github
import run
import setup_github_app
import setup_llm
import setup_project


def confirm(prompt: str, default_yes: bool = True) -> bool:
    suffix = "Y/n" if default_yes else "y/N"
    answer = input(f"{prompt} [{suffix}]: ").strip().lower()
    if not answer:
        return default_yes
    return answer.startswith("y")


def run_step(label: str, step_main) -> bool:
    """Runs a sub-script's main() as one guided step. These scripts call
    sys.exit() themselves on failure (e.g. a bad API key, docker missing) -
    catching that here lets the orchestrator report it and let the user
    decide whether to retry that step, rather than the whole orchestrator
    process dying with them."""
    print()
    print(f"=== {label} ===")
    try:
        step_main()
        return True
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        if code != 0:
            print(f"({label} exited with an error - see above.)")
        return code == 0
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"({label} failed unexpectedly: {e})")
        return False


def run_guided_step(label: str, step_main) -> bool:
    """run_step, but offers to retry on failure instead of just reporting
    it and moving on - for the two interactive config steps, where "try
    again" is almost always what the user wants (a mistyped key, a
    docker-not-running moment they've since fixed), not "skip and hope
    later steps compensate"."""
    while True:
        if run_step(label, step_main):
            return True
        if not confirm(f"Retry {label}?", default_yes=True):
            return False


def run_doctor_gate(repo_root: Path) -> bool:
    """Runs doctor.check() and loops: show the full punch list, let the
    user go fix things by hand and retry, or give up. Returns True once
    there are no more ERROR-level items - warnings are shown but never
    block (same philosophy as doctor.py itself: only errors are a hard
    gate on running the agent)."""
    while True:
        print()
        print("=== doctor.py (gate) ===")
        result = doctor.check(repo_root)
        if not result.has_errors:
            return True
        print()
        print("Doctor found problems that must be fixed before the agent can run (see the")
        print("ERROR items above) - fix them (by hand, or by re-running an earlier step),")
        if not confirm("then check again?", default_yes=True):
            return False


def github_auth_configured(env_path: Path) -> bool:
    """True iff .env has a usable GitHub auth method - GITHUB_TOKEN, or all
    three GITHUB_APP_* vars (the same rule doctor.py and lib_github use)."""
    env = lib_env.load_env_file(env_path) if Path(env_path).is_file() else {}
    return bool(env.get("GITHUB_TOKEN")) or lib_github.github_app_fully_configured(env)


def _print_github_auth_instructions() -> None:
    print("The agent needs GitHub access to the target repo. Configure ONE of these in .env:")
    print("  - GitHub App: python3 setup_github_app.py  (guided; see docs/GITHUB-INTEGRATION.md \"Option 2\")")
    print("  - Personal Access Token: set GITHUB_TOKEN (fine-grained, Contents/Issues/Pull requests read+write)")
    print("Then re-run python3 setup_all.py (or python3 doctor.py) to verify.")


def _setup_github_pat(env_path: Path) -> bool:
    """Prompts (hidden) for a PAT and writes it to the git-ignored .env only.
    Verifies it against GITHUB_REPO_URL when that's set - best effort: a
    failed check is reported but the token is still saved (doctor.py
    re-checks it on the next step)."""
    print("Create a fine-grained token at https://github.com/settings/personal-access-tokens/new")
    print("with read+write access to Contents, Issues and Pull requests on the target repo.")
    token = getpass.getpass("Paste your GitHub Personal Access Token (input hidden, empty to cancel): ").strip()
    if not token:
        print("No token entered.")
        return False
    lib_env.update_env_var(env_path, "GITHUB_TOKEN", token)
    print("GITHUB_TOKEN written to .env.")
    parsed = lib_github.parse_owner_repo(lib_env.read_env_var(env_path, "GITHUB_REPO_URL"))
    if parsed:
        ok, detail = lib_github.check_repo_access(parsed[0], parsed[1], token)
        print(f"GitHub access: {detail}" if ok else f"WARNING: GitHub access: {detail}")
    return True


def ensure_github_auth(env_path: Path, skip: bool = False, interactive: bool = None) -> bool:
    """Step 4 (GH issue #565 / SPEC-ISSUE-0100). Returns True once .env has
    GitHub auth configured. Never prompts when skip is set or stdin isn't a
    TTY - prints how to configure it instead and returns False, so a
    scripted run doesn't hang on input() and offer_to_start can warn."""
    print()
    print("=== GitHub authentication ===")
    if github_auth_configured(env_path):
        print("GitHub authentication already configured in .env - skipping.")
        return True
    if interactive is None:
        interactive = sys.stdin.isatty()
    if skip or not interactive:
        print("WARNING: No GitHub authentication configured in .env (GITHUB_TOKEN or GITHUB_APP_*).")
        _print_github_auth_instructions()
        return False

    while True:
        print("No GitHub authentication configured yet. How should the agent authenticate to GitHub?")
        print("  1) GitHub App (recommended - a dedicated bot identity; guided browser flow)")
        print("  2) Personal Access Token (simplest - acts as your own account)")
        print("  3) Skip for now (the agent will NOT be able to work on the target repo)")
        choice = input("Choice [1]: ").strip() or "1"
        if choice == "1":
            run_step("setup_github_app.py (GitHub App)", lambda: setup_github_app.main([], env_path=env_path))
        elif choice == "2":
            _setup_github_pat(env_path)
        elif choice == "3":
            _print_github_auth_instructions()
            return False
        else:
            print(f"Unrecognized choice {choice!r}.")
            continue
        if github_auth_configured(env_path):
            return True
        if not confirm("GitHub authentication still isn't configured. Try again?", default_yes=True):
            _print_github_auth_instructions()
            return False


def offer_to_start(dev: bool, github_auth_ok: bool = True) -> None:
    """dev was already decided upfront in main() (step 0) - not re-asked
    here, since by this point it's too late for that answer to affect
    anything (any container work dev mode would want to precede has
    already happened in earlier steps). github_auth_ok=False (step 4 was
    skipped/declined) flips the default to "don't start" behind a loud
    warning rather than calling the setup "ready" (GH issue #565)."""
    print()
    if github_auth_ok:
        print("--- Ready to start the agent ---")
    else:
        print("--- WARNING: GitHub authentication is NOT configured ---")
        print("The agent will start, but cannot read or write the target repo (issues, PRs, pushes).")
        _print_github_auth_instructions()
    if not confirm("Start the agent now?", default_yes=github_auth_ok):
        print("You can start it later with: python3 run.py")
        return

    mode = "cli" if confirm("Interactive CLI mode instead of the web UI?", default_yes=False) else "web"
    daemon = confirm("Run detached (daemon) instead of in the foreground?", default_yes=False)

    argv = [mode]
    if daemon:
        argv.append("daemon")
    if dev:
        argv.append("dev")

    # A leftover stack from an earlier run (or from switching between
    # docker-compose.yaml and docker-compose.local.yaml, which share the
    # same default project name and several service names) can make
    # `docker compose up` fail outright with no obvious cause - offer a
    # controlled reset before that happens (GH discussion on local Ollama
    # setups). run.py itself stays non-interactive - see its own docstring -
    # so this lives here instead, and only in developer mode: that's the
    # case that just rebuilt images and is most likely to collide with a
    # stack still running under the old ones. `cli` mode is exempt too, same
    # as it always was: `docker compose run` starts a one-off container, not
    # an `up` that could conflict with one already running.
    if dev and mode != "cli":
        full_compose_args = run.compose_file_args(Path(".")) + lib_docker.compose_project_args("dev")
        lib_docker.maybe_stop_existing_stack(full_compose_args)

    print(f"--- Handing off to: python3 run.py {' '.join(argv)} ---")
    run.main(argv)  # run.py owns the process from here, including sys.exit


def main() -> None:
    default_dev = "--dev" in sys.argv[1:] or "dev" in sys.argv[1:]
    skip_github_auth = "--skip-github-auth" in sys.argv[1:]

    banner.print_banner()
    print("--- Horseless Carriage: Guided Setup ---")
    print("This walks through every setup step in order. Each step is also its own")
    print("standalone script (setup_llm.py, setup_project.py, doctor.py, run.py) if you")
    print("ever want to re-run just one of them directly.")

    # Step 0: developer mode, decided before any container work happens -
    # see this module's own docstring for why this can't wait until the
    # end (offer_to_start used to ask this, after setup_llm.py's Local/
    # Ollama live test had already started a container dev mode would
    # rebuild).
    dev = confirm(
        "Enable developer mode? (rebuilds agent/ollama images fresh, verbose logs)",
        default_yes=default_dev,
    )

    repo_root = Path(__file__).resolve().parent

    if not run_guided_step("setup_llm.py (LLM provider/model)", lambda: setup_llm.main(dev=dev)):
        print("Stopping here - re-run python3 setup_all.py (or python3 setup_llm.py directly) when ready.")
        sys.exit(1)

    if not run_guided_step("check_state_repo.py (state repository)", check_state_repo.main):
        print("Stopping here - re-run python3 setup_all.py (or python3 check_state_repo.py directly) when ready.")
        sys.exit(1)

    if not run_guided_step("setup_project.py (Docker/GitHub CLI)", setup_project.main):
        print("Stopping here - re-run python3 setup_all.py (or python3 setup_project.py directly) when ready.")
        sys.exit(1)

    github_auth_ok = ensure_github_auth(repo_root / ".env", skip=skip_github_auth)

    if not run_doctor_gate(repo_root):
        print()
        print("Not proceeding to start the agent until the ERROR items above are fixed.")
        print("Re-run python3 setup_all.py (or python3 doctor.py directly) once they are.")
        sys.exit(1)

    offer_to_start(dev, github_auth_ok=github_auth_ok)


if __name__ == "__main__":
    main()
