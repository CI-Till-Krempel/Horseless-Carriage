#!/usr/bin/env python3
"""
Runs the ADK-native gate-enforcement eval set (eval/adk/scrum_team.evalset.json)
against your currently configured provider/model, inside the same `agent`
container image the project already builds.

This is a much smaller, much cheaper check than the full scenario-based
harness (docs/EVALUATION.md, agents/scrum_team/scripts/run_eval.py): a
handful of scripted single/few-turn conversations checking whether specific,
already unit-tested, mechanically-enforced gates (protected-branch push
refusal, story-pipeline stage skipping, missing-approval blocks, etc.) also
hold up against a live model's actual tool-call behavior - see
eval/adk/README.md for the full picture, including known limitations of
exact tool-call argument matching.

Requires a real .env (a configured provider + LITELLM_MASTER_KEY) - unlike
the host-script test suite, this sends real requests to your configured
model, so it costs real tokens/money (or real local compute for Ollama).

Usage:
  python3 run_adk_eval.py             Run the eval set for real.
  python3 run_adk_eval.py --dry-run   Print the command that would run, without executing it.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import lib_docker

EVAL_SET_PATH = "eval/adk/scrum_team.evalset.json"
EVAL_CONFIG_PATH = "eval/adk/test_config.json"
AGENT_MODULE_PATH = "eval/adk/agent/scrum_team"


def parse_args(argv: list) -> bool:
    """Returns whether --dry-run was passed."""
    return "--dry-run" in argv


def hc_version_and_commit() -> tuple:
    """(version, commit) this eval set is actually about to run against -
    GH issue #167/#168: printed before anything else so it's clear which
    build/commit produced a given run's results. Read directly from the
    host checkout (VERSION file, `git rev-parse HEAD`) rather than relying
    on an HC_COMMIT_SHA env var (unlike the team-performance harness's
    run_eval.py, which runs *inside* the agent container - whose image
    deliberately excludes .git, see .dockerignore/GH issue #123 - this
    script runs on the host, where both are directly available)."""
    try:
        version = Path("VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        version = "unknown"
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        commit = "unknown"
    return version, commit


def adk_eval_command() -> list:
    """The `adk eval` invocation run inside the container - see
    eval/adk/README.md's "Deviation: a loader shim was required" for why
    AGENT_MODULE_PATH is the eval/adk/agent/scrum_team shim rather than
    agents/scrum_team or agents directly."""
    return [
        "adk", "eval", AGENT_MODULE_PATH, EVAL_SET_PATH,
        "--config_file_path", EVAL_CONFIG_PATH,
        "--print_detailed_results",
    ]


def main() -> None:
    os.chdir(Path(__file__).resolve().parent)
    dry_run = parse_args(sys.argv[1:])

    version, commit = hc_version_and_commit()
    print(f"--- Horseless Carriage v{version} (commit {commit}) ---")

    if shutil.which("docker") is None:
        print("ERROR: 'docker' command not found. Please install Docker.")
        sys.exit(1)

    env_path = Path(".env")
    if not env_path.is_file():
        print("ERROR: .env not found. Run python3 setup_llm.py first - this eval set needs a")
        print("real configured provider/model, not just the test suite's mock model.")
        sys.exit(1)

    # GH issue #169: its own Compose project name (horseless-carriage-eval),
    # distinct from the dev stack's (run.py) and the test suite's
    # (run_tests.py) - see lib_docker.compose_project_args.
    compose_args = lib_docker.compose_file_args(Path(".")) + lib_docker.compose_project_args("eval")
    adk_cmd = adk_eval_command()

    if dry_run:
        print("Would run:")
        print(f"  docker compose {' '.join(compose_args)} --env-file .env up -d db litellm")
        print(f"  docker compose {' '.join(compose_args)} --env-file .env run --rm -e LOG_LEVEL=debug --entrypoint \"\" agent \\")
        print(f"    {' '.join(adk_cmd)}")
        return

    print("--- Bringing up db + litellm ---")
    up_cmd = ["docker", "compose", *compose_args, "--env-file", ".env", "up", "-d", "db", "litellm"]
    result = subprocess.run(up_cmd)
    if result.returncode != 0:
        sys.exit(result.returncode)

    print(f"--- Running ADK eval set: {EVAL_SET_PATH} ---")
    # LOG_LEVEL=debug overridden here, not in .env - this eval set exists to
    # catch gate-enforcement regressions against a live model, so seeing the
    # full request/response trace (e.g. the exact LiteLLM error behind a
    # canned "[CONNECTION ERROR]" response, see agent.py's
    # _patched_adk_acompletion) matters every time it's run; the normal dev
    # stack (run.py) shouldn't get that verbosity by default just because
    # it shares the same .env file.
    run_cmd = [
        "docker", "compose", *compose_args, "--env-file", ".env", "run", "--rm",
        "-e", "LOG_LEVEL=debug", "--entrypoint", "", "agent", *adk_cmd,
    ]
    result = subprocess.run(run_cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
