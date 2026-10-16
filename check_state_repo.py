#!/usr/bin/env python3
"""
Checks if the state repository is in the expected state for the tools to
work.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import lib_env

_HISTORY_WALK_DEPTH = 50

# Must match agents/scrum_team/scripts/validate_state.py's own
# CORRUPTION_EXIT_CODE - kept as a literal here (not imported) since that
# module's own top-level `from agents.scrum_team.state import ScrumState`
# would sys.exit() at import time on a host Python that doesn't have
# pydantic/the agents package installed, which is exactly the "couldn't
# even run" case this constant exists to distinguish from real corruption
# (see GH issue #109).
VALIDATE_STATE_CORRUPTION_EXIT_CODE = 3


def stray_template_files(specs_dir: Path) -> list:
    """TEMPLATE-*.md files directly under specs_dir - these belong only in
    the main project's spec-templates directory, never copied into a state
    repository. Reused by doctor.py's cheap, always-on version of this
    check (GH issue #60), separately from the heavier state.json
    validation below, which only this standalone script runs."""
    return sorted(specs_dir.glob("TEMPLATE-*.md"))


def ensure_role_identity_defaults(state_repo_path: Path) -> dict:
    """
    Migration for state repos that predate the per-role
    `<Role>-identity.md` convention (see agents/scrum_team/prompts.py's
    module docstring, tools/workflow.py's propose_steering_change, and
    docs/STATE-REPOSITORY.md) - materializes each role's default identity
    content as a real, editable file in the state repo, so a human
    browsing it can find and hand-edit it directly instead of it only ever
    existing as an invisible runtime fallback.

    Note this is a convenience, not a correctness requirement: agent.py's
    role_identity_injection_callback already falls back to each role's
    `<Role>-identity.default.md` (this project's own prompt_modules/) at
    runtime when the state repo has no `<Role>-identity.md` of its own -
    every role works correctly with or without this migration ever having
    run. What this adds is a real file a team can discover and start
    editing without first learning that fallback exists.

    Per role, in ROLE_NAMES (agents/scrum_team/prompts.py):
    - `<Role>-identity.md` missing entirely: created with that role's
      default identity content, verbatim.
    - `<Role>-identity.md` already exists (any content, including empty):
      left completely untouched - unlike the shared-file AGENTS.md
      migration this replaces, each role has its own dedicated file, so
      there's no "existing unrelated content" case to merge around; an
      existing file always means either a prior migration or a real
      customization already in place, and either way it's not this
      function's place to touch it.

    Returns {role: "created" | "already-present"} for every role in
    ROLE_NAMES. Safe to call on every check_state_repo.py/doctor.py
    invocation - each role's own result is independently idempotent.
    """
    from agents.scrum_team.prompts import ROLE_NAMES, load_role_identity_default

    results = {}
    for role in ROLE_NAMES:
        identity_path = state_repo_path / f"{role}-identity.md"
        if identity_path.is_file():
            results[role] = "already-present"
            continue
        identity_path.write_text(load_role_identity_default(role), encoding="utf-8")
        results[role] = "created"
    return results


_WORKBENCH_SCRIPT_PLACEHOLDER = (
    "#!/bin/sh\n"
    "# SPEC-ISSUE-0064: per-project \"workbench\" - whatever toolchain THIS\n"
    "# product's tech stack needs (JDK, Node, Go, ...) gets installed here,\n"
    "# e.g. `apt-get update && apt-get install -y default-jdk`.\n"
    "#\n"
    "# Never hand-edit this file directly - it's built into the agent image\n"
    "# at every rebuild, so a direct edit here has no effect until it's\n"
    "# actually committed and the image is rebuilt, and (more importantly)\n"
    "# the Architect's propose_framework_change tool is the only way this\n"
    "# is meant to change: it always opens a human-reviewed Pull Request,\n"
    "# the same way propose_steering_change does for <Role>-identity.md -\n"
    "# see agents/scrum_team/tools/workflow.py.\n"
    "#\n"
    "# No workbench-specific setup yet - this placeholder is a harmless\n"
    "# no-op so the agent image still builds for a project with no\n"
    "# toolchain needs beyond horseless-carriage's own Python base.\n"
)


def ensure_workbench_script_default(state_repo_path: Path) -> str:
    """
    SPEC-ISSUE-0064: guarantees `.hc/workbench-setup.sh` always exists in the
    state repo, so the agent image's build (agent.Dockerfile's `COPY
    --from=workbench workbench-setup.sh ...`, docker-compose.yaml's
    `additional_contexts` pointed at `<STATE_REPO_PATH>/.hc`) never fails
    for a project that has no workbench yet - same idempotent,
    create-only-if-missing shape as ensure_role_identity_defaults above
    (never overwrites a real one, including an empty file - that's either a
    prior migration or a deliberate choice, not this function's place to
    touch).

    Returns "created" or "already-present".
    """
    workbench_path = state_repo_path / ".hc" / "workbench-setup.sh"
    if workbench_path.is_file():
        return "already-present"
    workbench_path.parent.mkdir(parents=True, exist_ok=True)
    workbench_path.write_text(_WORKBENCH_SCRIPT_PLACEHOLDER, encoding="utf-8")
    return "created"


def is_workbench_script_placeholder(state_repo_path: Path) -> bool:
    """True if `.hc/workbench-setup.sh` is missing entirely or still exactly
    the auto-created placeholder - i.e. the Architect hasn't customized it
    for this project's actual tech stack yet. Used by doctor.py to nudge
    toward `propose_framework_change` instead of leaving a silent gap."""
    workbench_path = state_repo_path / ".hc" / "workbench-setup.sh"
    if not workbench_path.is_file():
        return True
    return workbench_path.read_text(encoding="utf-8", errors="replace") == _WORKBENCH_SCRIPT_PLACEHOLDER


def _walk_git_history_for_valid_state_json(state_repo_path: Path) -> str:
    """Host-side equivalent of agents/scrum_team/tools/scrum.py's
    _recover_state_json_from_git - walks .hc/state.json's git history
    (newest first, up to _HISTORY_WALK_DEPTH commits) for the most recent
    commit whose own snapshot still parses as valid JSON, not just HEAD (a
    corrupted checkpoint can itself have been committed before anyone
    noticed). Returns that snapshot's raw text, or None if state_repo_path
    isn't a git repo, has no commits touching that path, or none of them
    parse either. Pure host-side git/subprocess - this script runs before
    the container even exists, so it can't call into agents/scrum_team's
    own copy of this logic."""
    if not (state_repo_path / ".git").exists():
        return None
    log = subprocess.run(
        ["git", "log", "--format=%H", "--", ".hc/state.json"],
        cwd=state_repo_path, capture_output=True, text=True,
    )
    if log.returncode != 0:
        return None
    shas = [line for line in log.stdout.splitlines() if line.strip()][:_HISTORY_WALK_DEPTH]
    for sha in shas:
        show = subprocess.run(
            ["git", "show", f"{sha}:.hc/state.json"],
            cwd=state_repo_path, capture_output=True, text=True,
        )
        if show.returncode != 0:
            continue
        try:
            json.loads(show.stdout)
        except Exception:
            continue
        return show.stdout
    return None


def _rebuild_state_from_specs(repo_root: Path, state_repo_path: Path) -> int:
    """SPEC-ISSUE-0052 last resort: re-derive state.json from whatever specs/
    already has on disk (the same doc-sync step init_scrum_state() runs on
    a session's first-ever init - see agents/scrum_team/scripts/
    rebuild_state_from_specs.py), for when neither resetting to a git
    checkpoint nor deleting-and-restarting actually recovers anything
    useful. Prefers Docker (so pydantic/the agents package are guaranteed
    present), same preference order as the state.json validation step in
    run() above."""
    script = "agents/scrum_team/scripts/rebuild_state_from_specs.py"
    if shutil.which("docker") is not None and (repo_root / "docker-compose.yaml").is_file():
        print("Running rebuild via Docker...")
        result = subprocess.run([
            "docker", "compose", "run", "--rm", "agent",
            "python3", script, "/app/state_repo",
        ], cwd=repo_root)
    elif shutil.which("python3") is not None:
        print("Running rebuild via local python3...")
        result = subprocess.run(
            ["python3", str(repo_root / script), str(state_repo_path)],
            cwd=repo_root,
        )
    else:
        print("ERROR: Could not find Docker or python3 to run the rebuild.")
        return 1
    return result.returncode


def _offer_state_repair(state_file: Path, state_repo_path: Path, repo_root: Path = None, prompt=input) -> int:
    """GH issue #85 - "Offer options to repair or delete corrupted state":
    the interactive remediation menu for a state.json that just failed
    validation. `prompt` is injectable so tests can drive this without a
    real terminal."""
    print()
    print("What would you like to do?")
    print("  1) Reset to the last known-good state found in git history")
    print("  2) Delete state.json and start fresh (the agent reinitializes on its next session)")
    print("  3) Leave it as-is (fix it manually, or start the agent and let the Orchestrator")
    print("     attempt an LLM-assisted repair in chat - see docs/STATE-REPOSITORY.md)")
    print("  4) Last resort: rebuild state.json from specs/ on disk (re-derives stories/epics/")
    print("     PRD vision+goals/architecture from the markdown already there - use this if")
    print("     options 1 and 2 aren't viable, e.g. no usable git checkpoint exists either)")
    choice = prompt("Choice [3]: ").strip() or "3"

    if choice == "1":
        recovered = _walk_git_history_for_valid_state_json(state_repo_path)
        if recovered is None:
            print("ERROR: No usable checkpoint found anywhere in git history for .hc/state.json.")
            return 1
        state_file.write_text(recovered, encoding="utf-8")
        print("Restored state.json from the last known-good checkpoint in git history.")
        return 0

    if choice == "2":
        state_file.unlink()
        print("Deleted the corrupted state.json - the team will start fresh next session.")
        return 0

    if choice == "4":
        return _rebuild_state_from_specs(repo_root, state_repo_path)

    print("Leaving state.json as-is.")
    return 1


def run(repo_root: Path, interactive: bool = None, prompt=input) -> int:
    """Runs every check against repo_root (no chdir, so this is safe to call
    from tests against a tmp_path fixture). Returns a process exit code.

    interactive defaults to sys.stdin.isatty() (a real terminal) - pass an
    explicit True/False to override (tests do this, since pytest's stdin
    isn't a tty either way and shouldn't accidentally block on input()).
    prompt is threaded down to _offer_state_repair, so tests can drive the
    repair menu without a real terminal too."""
    repo_root = Path(repo_root)
    print("--- Checking State Repository ---")

    # 1. Load environment variables from .env
    env_path = repo_root / ".env"
    if not env_path.is_file():
        print("ERROR: .env file not found. Please copy .env.example to .env and fill in the values.")
        return 1

    # 2. Check for the existence of the state repository path
    state_repo_path_str = lib_env.read_env_var(env_path, "STATE_REPO_PATH")
    state_repo_path = Path(state_repo_path_str).expanduser() if state_repo_path_str else None
    if not state_repo_path_str or not state_repo_path.is_dir():
        print(f"ERROR: STATE_REPO_PATH is not set or the directory does not exist: {state_repo_path_str}")
        print("Please create this directory and ensure it is correctly set in your .env file.")
        return 1

    print(f"State repository found at: {state_repo_path}")

    # 3. Verify directory structure
    specs_dir = state_repo_path / "specs"
    if not specs_dir.is_dir():
        print("ERROR: The 'specs' directory is missing from the state repository.")
        print(f"Please create it: mkdir -p {specs_dir}")
        return 1

    print("  [OK] 'specs' directory exists.")

    # 4. Check for stray templates
    stray_templates = stray_template_files(specs_dir)
    if stray_templates:
        print("WARNING: Found template files in the state repository. These should only be in the main project's 'spec-templates' directory.")
        print("Please remove the following files from your state repository:")
        for f in stray_templates:
            print(f)
    else:
        print("  [OK] No stray templates found in 'specs' directory.")

    # 5. Migrate/verify each role's default identity.md exists - additive
    # only, never overwrites an existing file (see
    # ensure_role_identity_defaults's own docstring).
    identity_results = ensure_role_identity_defaults(state_repo_path)
    created = sorted(role for role, result in identity_results.items() if result == "created")
    if created:
        print(f"  [OK] Created default identity.md for {len(created)} role(s) not yet customized: {', '.join(created)}.")
    else:
        print("  [OK] Every role already has its own identity.md.")

    # 5b. Migrate/verify .hc/workbench-setup.sh exists - same additive-only
    # shape (see ensure_workbench_script_default's own docstring), required
    # for the agent image's build (agent.Dockerfile's workbench context).
    if ensure_workbench_script_default(state_repo_path) == "created":
        print("  [OK] Created default .hc/workbench-setup.sh (no workbench-specific setup yet).")
    else:
        print("  [OK] .hc/workbench-setup.sh already exists.")

    # 6. Validate state.json structure
    state_file = state_repo_path / ".hc" / "state.json"
    if state_file.is_file():
        print("--- Validating state.json ---")

        validation_exit_code = 0
        if shutil.which("docker") is not None and (repo_root / "docker-compose.yaml").is_file():
            # Run via Docker to ensure all dependencies (pydantic) are present.
            print("Running validation via Docker...")
            result = subprocess.run([
                "docker", "compose", "run", "--rm", "agent",
                "python3", "agents/scrum_team/scripts/validate_state.py",
                "/app/state_repo/.hc/state.json",
            ], cwd=repo_root)
            validation_exit_code = result.returncode
        elif shutil.which("python3") is not None:
            print("Running validation via local python3...")
            result = subprocess.run(
                ["python3", str(repo_root / "agents/scrum_team/scripts/validate_state.py"), str(state_file)],
                cwd=repo_root,
            )
            validation_exit_code = result.returncode
        else:
            print("WARNING: Could not find Docker or python3 to validate state.json.")

        if validation_exit_code == VALIDATE_STATE_CORRUPTION_EXIT_CODE:
            print("ERROR: state.json validation failed. Your state might be corrupted or outdated.")
            is_interactive = sys.stdin.isatty() if interactive is None else interactive
            if not is_interactive:
                print("If you recently updated the project, you might need to manually fix the state.json")
                print("or initialize a new one, or re-run this interactively for repair options.")
                return 1
            return _offer_state_repair(state_file, state_repo_path, repo_root=repo_root, prompt=prompt)
        elif validation_exit_code != 0:
            # Anything other than 0 (valid) or CORRUPTION_EXIT_CODE (genuine
            # content corruption) means the validation script itself
            # couldn't run - a missing dependency (validate_state.py's own
            # ImportError), the Docker daemon being down, or any other
            # environment problem (GH issue #109). This is NOT evidence
            # state.json is actually corrupted, so it must not offer the
            # repair/reset/delete menu - a real, healthy state file must
            # never be reset or deleted because of an unrelated environment
            # issue.
            print("WARNING: Could not validate state.json (the validation script itself did not run - "
                  "this is not necessarily a sign state.json is corrupted).")
            print("Check that Docker is running (if using the Docker validation path) or that "
                  "pydantic is installed (if using the local python3 fallback), then try again.")
    else:
        print("INFO: .hc/state.json not found. This is normal if the agent hasn't run yet.")

    print()
    print("--- State Repository Check Complete ---")
    print("The state repository appears to be in a valid state.")
    return 0


def main() -> None:
    sys.exit(run(Path(__file__).resolve().parent))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # GH issue #117 - see setup_llm.py's own handler for the full
        # rationale. Only wraps the standalone entry point - setup_all.py
        # calls main() directly and wants Ctrl+C to abort its whole guided
        # flow, not just this one step.
        print()
        print("Cancelled.")
        sys.exit(0)
