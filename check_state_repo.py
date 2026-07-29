#!/usr/bin/env python3
"""
Checks if the state repository is in the expected state for the tools to
work.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import lib_env


def stray_template_files(specs_dir: Path) -> list:
    """TEMPLATE-*.md files directly under specs_dir - these belong only in
    the main project's spec-templates directory, never copied into a state
    repository. Reused by doctor.py's cheap, always-on version of this
    check (GH issue #60), separately from the heavier state.json
    validation below, which only this standalone script runs."""
    return sorted(specs_dir.glob("TEMPLATE-*.md"))


def run(repo_root: Path) -> int:
    """Runs every check against repo_root (no chdir, so this is safe to call
    from tests against a tmp_path fixture). Returns a process exit code."""
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

    # 5. Validate state.json structure
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

        if validation_exit_code != 0:
            print("ERROR: state.json validation failed. Your state might be corrupted or outdated.")
            print("If you recently updated the project, you might need to manually fix the state.json")
            print("or initialize a new one.")
            return 1
    else:
        print("INFO: .hc/state.json not found. This is normal if the agent hasn't run yet.")

    print()
    print("--- State Repository Check Complete ---")
    print("The state repository appears to be in a valid state.")
    return 0


def main() -> None:
    sys.exit(run(Path(__file__).resolve().parent))


if __name__ == "__main__":
    main()
