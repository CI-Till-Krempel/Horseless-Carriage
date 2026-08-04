#!/usr/bin/env python3
"""
Runs the ADK-native gate-enforcement eval set (eval/adk/scrum_team.evalset.json)
against a dedicated, reproducible model config - NOT whatever provider/model
this developer's own .env/config/model-templates/*.yaml currently happen to
be configured for - inside the same `agent` container image the project
already builds.

This is a much smaller, much cheaper check than the full scenario-based
harness (docs/EVALUATION.md, agents/scrum_team/scripts/run_eval.py): a
handful of scripted single/few-turn conversations checking whether specific,
already unit-tested, mechanically-enforced gates (protected-branch push
refusal, story-pipeline stage skipping, missing-approval blocks, etc.) also
hold up against a live model's actual tool-call behavior - see
eval/adk/README.md for the full picture, including known limitations of
exact tool-call argument matching.

Two dedicated LiteLLM configs (see "Reproducible model config" below), never
touched by setup_llm.py, keep results comparable across machines/runs
instead of silently depending on whichever provider/model a developer's own
dev stack was last configured for - a real run once failed outright because
config/model-templates/litellm.local-ollama.yaml had drifted out of sync
with this developer's own .env.

Requires a real .env (a configured provider + LITELLM_MASTER_KEY in local
mode; --ci mode instead needs GOOGLE_API_KEY/LITELLM_MASTER_KEY in whatever
--env-file is given) - unlike the host-script test suite, this sends real
requests to a real model, so it costs real tokens/money (or real local
compute for Ollama).

Reproducible model config:
  Local (default): eval/adk/litellm.local.yaml, pinned to Ollama's
  llama3.1:8b regardless of this developer's own OLLAMA_MODEL - runs against
  docker-compose.local.yaml (self-hosted, no API key needed).
  --ci: eval/adk/litellm.ci.yaml, every role pointed at the same cheap
  Gemini model - runs against docker-compose.yaml (cloud stack, no Ollama).
  Used by .github/workflows/adk-eval.yml on every release tag, with
  GOOGLE_API_KEY injected from a GitHub Actions repo secret.

The eval's own docker compose stack (db/litellm/ollama) is always torn down
afterward, success or failure - see the `finally` block in main().

Usage:
  python3 run_adk_eval.py                      Run the eval set locally (pinned Ollama model).
  python3 run_adk_eval.py --ci                 Run against the cheap cloud model (see adk-eval.yml).
  python3 run_adk_eval.py --env-file .env.foo  Use a different env file for docker compose.
  python3 run_adk_eval.py --dry-run            Print the commands that would run, without running them.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import lib_docker

EVAL_SET_PATH = "eval/adk/scrum_team.evalset.json"
EVAL_CONFIG_PATH = "eval/adk/test_config.json"
AGENT_MODULE_PATH = "eval/adk/agent/scrum_team"

# Pinned, dedicated configs - see this module's docstring's "Reproducible
# model config". Neither is ever touched by setup_llm.py (which only
# rewrites litellm.yaml / config/model-templates/litellm.*.yaml).
LOCAL_LITELLM_CONFIG = "./eval/adk/litellm.local.yaml"
LOCAL_OLLAMA_MODEL = "llama3.1:8b"
CI_LITELLM_CONFIG = "./eval/adk/litellm.ci.yaml"


def parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Print the commands that would run, without executing them.")
    parser.add_argument(
        "--ci", action="store_true",
        help=(
            "Use the cheap cloud-model config (eval/adk/litellm.ci.yaml) against the cloud "
            "docker-compose.yaml stack (no Ollama), instead of the pinned local Ollama config - "
            "see .github/workflows/adk-eval.yml, which passes this on every release tag."
        ),
    )
    parser.add_argument(
        "--env-file", default=".env",
        help="Path to the env file docker compose reads secrets/config from (default: .env).",
    )
    return parser.parse_args(argv)


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


def compose_setup(ci: bool) -> tuple:
    """(compose_args, extra_env) for this run.

    Deliberately does NOT use lib_docker.compose_file_args() (which follows
    whatever provider/model this developer's own .env/setup_llm.py last
    configured for the regular dev stack) - a real run once broke outright
    because that shared config had drifted out of sync with this developer's
    own OLLAMA_MODEL. Local mode always targets docker-compose.local.yaml +
    LOCAL_LITELLM_CONFIG/LOCAL_OLLAMA_MODEL; --ci always targets the cloud
    docker-compose.yaml (no Ollama) + CI_LITELLM_CONFIG - identical,
    reproducible model config regardless of the calling machine's own setup.

    extra_env overrides LITELLM_CONFIG_PATH (see docker-compose.yaml/
    docker-compose.local.yaml's parameterized litellm volume mount) and, in
    local mode, OLLAMA_MODEL too, so the `ollama` container actually pulls
    the same model this run's LiteLLM config expects.
    """
    if ci:
        return (
            lib_docker.compose_project_args("eval"),
            {"LITELLM_CONFIG_PATH": CI_LITELLM_CONFIG},
        )
    return (
        ["-f", "docker-compose.local.yaml"] + lib_docker.compose_project_args("eval"),
        {"LITELLM_CONFIG_PATH": LOCAL_LITELLM_CONFIG, "OLLAMA_MODEL": LOCAL_OLLAMA_MODEL},
    )


def main() -> None:
    os.chdir(Path(__file__).resolve().parent)
    args = parse_args(sys.argv[1:])

    version, commit = hc_version_and_commit()
    print(f"--- Horseless Carriage v{version} (commit {commit}) ---")

    if shutil.which("docker") is None:
        print("ERROR: 'docker' command not found. Please install Docker.")
        sys.exit(1)

    env_path = Path(args.env_file)
    if not env_path.is_file():
        print(f"ERROR: {args.env_file} not found. Run python3 setup_llm.py first - this eval set needs a")
        print("real configured provider/model, not just the test suite's mock model.")
        sys.exit(1)

    # GH issue #169: its own Compose project name (horseless-carriage-eval),
    # distinct from the dev stack's (run.py) and the test suite's
    # (run_tests.py) - see lib_docker.compose_project_args.
    compose_args, extra_env = compose_setup(args.ci)
    adk_cmd = adk_eval_command()
    run_env = {**os.environ, **extra_env}
    env_prefix = " ".join(f"{k}={v}" for k, v in extra_env.items())

    up_cmd = ["docker", "compose", *compose_args, "--env-file", args.env_file, "up", "-d", "db", "litellm"]
    run_cmd = [
        "docker", "compose", *compose_args, "--env-file", args.env_file, "run", "--rm",
        "-e", "LOG_LEVEL=debug", "--entrypoint", "", "agent", *adk_cmd,
    ]
    down_cmd = ["docker", "compose", *compose_args, "--env-file", args.env_file, "down"]
    if args.ci:
        # Ephemeral CI runner - also drop named volumes, matching eval.yml's
        # own `down -v`. Kept locally (no -v) so the pinned Ollama model
        # doesn't need re-pulling on every subsequent local run.
        down_cmd.append("-v")

    if args.dry_run:
        print("Would run:")
        print(f"  {env_prefix} docker compose {' '.join(compose_args)} --env-file {args.env_file} up -d db litellm")
        print(f"  docker compose {' '.join(compose_args)} --env-file {args.env_file} run --rm -e LOG_LEVEL=debug --entrypoint \"\" agent \\")
        print(f"    {' '.join(adk_cmd)}")
        print(f"  {' '.join(down_cmd)}")
        return

    exit_code = 1
    try:
        print("--- Bringing up db + litellm ---")
        result = subprocess.run(up_cmd, env=run_env)
        if result.returncode != 0:
            exit_code = result.returncode
        else:
            print(f"--- Running ADK eval set: {EVAL_SET_PATH} ---")
            # LOG_LEVEL=debug overridden here, not in .env - this eval set
            # exists to catch gate-enforcement regressions against a live
            # model, so seeing the full request/response trace (e.g. the
            # exact LiteLLM error behind a canned "[CONNECTION ERROR]"
            # response, see agent.py's _patched_adk_acompletion) matters
            # every time it's run; the normal dev stack (run.py) shouldn't
            # get that verbosity by default just because it shares the same
            # .env file.
            result = subprocess.run(run_cmd, env=run_env)
            exit_code = result.returncode
    finally:
        # The stack was previously left running indefinitely after every
        # run (`restart: unless-stopped`, no teardown anywhere) - always
        # tear it down here, even if bringing it up or running the eval
        # itself failed/raised, so a local run doesn't accumulate stray
        # containers and a CI run doesn't leak a stack past the job.
        print("--- Tearing down the eval stack ---")
        subprocess.run(down_cmd, env=run_env)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
