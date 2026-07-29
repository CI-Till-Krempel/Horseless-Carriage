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
  4. doctor.py           - gate: shows the full punch list of anything left
                           to fix, and loops (fix -> retry) until there are
                           no more ERROR-level items, before proceeding.
  5. Offers to start the agent now via run.py, in whichever mode you want -
     developer mode itself was already decided in step 0.

Usage:
  python3 setup_all.py        Interactive, guided walkthrough of all of the above.
  python3 setup_all.py --dev  Same, but defaults step 0's developer-mode
                              question to yes instead of asking cold.
"""

import sys
from pathlib import Path

import check_state_repo
import doctor
import run
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


def offer_to_start(dev: bool) -> None:
    """dev was already decided upfront in main() (step 0) - not re-asked
    here, since by this point it's too late for that answer to affect
    anything (any container work dev mode would want to precede has
    already happened in earlier steps)."""
    print()
    print("--- Ready to start the agent ---")
    if not confirm("Start the agent now?", default_yes=True):
        print("You can start it later with: python3 run.py")
        return

    mode = "cli" if confirm("Interactive CLI mode instead of the web UI?", default_yes=False) else "web"
    daemon = confirm("Run detached (daemon) instead of in the foreground?", default_yes=False)

    argv = [mode]
    if daemon:
        argv.append("daemon")
    if dev:
        argv.append("dev")

    print(f"--- Handing off to: python3 run.py {' '.join(argv)} ---")
    run.main(argv)  # run.py owns the process from here, including sys.exit


def main() -> None:
    default_dev = "--dev" in sys.argv[1:] or "dev" in sys.argv[1:]

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

    if not run_doctor_gate(repo_root):
        print()
        print("Not proceeding to start the agent until the ERROR items above are fixed.")
        print("Re-run python3 setup_all.py (or python3 doctor.py directly) once they are.")
        sys.exit(1)

    offer_to_start(dev)


if __name__ == "__main__":
    main()
