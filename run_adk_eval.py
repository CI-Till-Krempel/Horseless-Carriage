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
  Local (default off macOS): eval/adk/litellm.local.yaml, pinned to
  Ollama's llama3.1:8b regardless of this developer's own OLLAMA_MODEL -
  runs against docker-compose.local.yaml (self-hosted, no API key needed,
  dockerized Ollama - CPU-only on Docker Desktop, see host-ollama below).
  host-ollama (default ON macOS - see resolve_host_ollama): same pinned
  model, but talks to Ollama running natively on this host instead of a
  dockerized `ollama` service - Docker Desktop (macOS/Windows) has no GPU
  passthrough at all (GH issue #93), so this is the only way a local run
  actually uses the GPU (Metal on macOS); same auto-default precedent as
  setup_llm.py's host_ollama_default_enable. Requires `ollama serve`
  already running on this host - this pulls the pinned model itself (via
  the host `ollama` CLI) if it isn't already present, before touching
  Docker at all. Force with --host-ollama; force the dockerized path
  instead (even on macOS) with --docker-ollama.
  --ci: eval/adk/litellm.ci.yaml, every role pointed at the same cheap
  Gemini model - runs against docker-compose.yaml (cloud stack, no Ollama).
  Used by .github/workflows/adk-eval.yml on every release tag, with
  GOOGLE_API_KEY injected from a GitHub Actions repo secret.

The eval's own docker compose stack (db/litellm/ollama) is always torn down
afterward, success or failure - see the `finally` block in main().

Usage:
  python3 run_adk_eval.py                      Run the eval set locally (pinned Ollama model - host-native on macOS, dockerized elsewhere).
  python3 run_adk_eval.py --host-ollama        Force a native Ollama on this host (GPU-accelerated on macOS) regardless of platform.
  python3 run_adk_eval.py --docker-ollama      Force the dockerized Ollama service even on macOS (CPU-only there).
  python3 run_adk_eval.py --ci                 Run against the cheap cloud model (see adk-eval.yml).
  python3 run_adk_eval.py --debug              Force LOG_LEVEL=debug for the eval run (verbose - see agent.py's logging).
  python3 run_adk_eval.py --env-file .env.foo  Use a different env file for docker compose.
  python3 run_adk_eval.py --dry-run            Print the commands that would run, without running them.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import lib_docker

EVAL_SET_PATH = "eval/adk/scrum_team.evalset.json"
EVAL_CONFIG_PATH = "eval/adk/test_config.json"
AGENT_MODULE_PATH = "eval/adk/agent/scrum_team"

# Pinned, dedicated configs - see this module's docstring's "Reproducible
# model config". None of these are ever touched by setup_llm.py (which only
# rewrites litellm.yaml / config/model-templates/litellm.*.yaml).
LOCAL_LITELLM_CONFIG = "./eval/adk/litellm.local.yaml"
HOST_OLLAMA_LITELLM_CONFIG = "./eval/adk/litellm.local-hostollama.yaml"
LOCAL_OLLAMA_MODEL = "llama3.1:8b"
CI_LITELLM_CONFIG = "./eval/adk/litellm.ci.yaml"

LITELLM_HEALTH_URL = "http://localhost:4000/health/readiness"
LITELLM_READY_TIMEOUT_SECONDS = 120
# llama3.1:8b is ~4.7GB - a slow connection can genuinely take several
# minutes on a first-ever pull (cached in the ollama_data volume for every
# run after that, see ollama-entrypoint.sh).
OLLAMA_MODEL_PULL_TIMEOUT_SECONDS = 1200


def parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Print the commands that would run, without executing them.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--ci", action="store_true",
        help=(
            "Use the cheap cloud-model config (eval/adk/litellm.ci.yaml) against the cloud "
            "docker-compose.yaml stack (no Ollama), instead of the pinned local Ollama config - "
            "see .github/workflows/adk-eval.yml, which passes this on every release tag."
        ),
    )
    mode_group.add_argument(
        "--host-ollama", action="store_true",
        help=(
            "Force talking to Ollama running natively on this host (docker-compose."
            "local-hostollama.yaml) instead of a dockerized `ollama` service - already the default "
            "on macOS (see resolve_host_ollama), since Docker Desktop has no GPU passthrough at "
            "all. Requires `ollama serve` already running; pulls the pinned model itself if needed."
        ),
    )
    mode_group.add_argument(
        "--docker-ollama", action="store_true",
        help=(
            "Force the dockerized `ollama` service even on macOS, opting out of the automatic "
            "host-Ollama default there (CPU-only on Docker Desktop, but no `ollama serve` "
            "prerequisite on the host)."
        ),
    )
    parser.add_argument(
        "--env-file", default=".env",
        help="Path to the env file docker compose reads secrets/config from (default: .env).",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help=(
            "Force LOG_LEVEL=debug for the eval run - this eval set exists to catch "
            "gate-enforcement regressions against a live model, so seeing the full "
            "request/response trace matters when actually diagnosing a failure, but it's "
            "too noisy to leave on by default for every run (off unless passed)."
        ),
    )
    return parser.parse_args(argv)


def resolve_host_ollama(args: argparse.Namespace, platform: str = None) -> bool:
    """Whether this run should talk to a native Ollama on this host instead
    of a dockerized `ollama` service. --host-ollama/--docker-ollama (a
    mutually exclusive pair - see parse_args) always win when passed
    explicitly; with neither passed, defaults to on for macOS only - same
    precedent as setup_llm.py's host_ollama_default_enable: Docker Desktop
    for Mac has no GPU passthrough at all (GH issue #93), even on Apple
    Silicon, so a dockerized Ollama there can never use the GPU. Elsewhere
    (Linux/Windows), the dockerized `ollama` service already works fine,
    with optional NVIDIA GPU passthrough via docker-compose.gpu.yaml, so
    there's no reason to default away from it.

    `platform` defaults to None, resolved to sys.platform *at call time*
    (not bound at def time, unlike a plain `platform: str = sys.platform`
    default) - this is what lets tests pin the platform-based default
    deterministically (either by passing this explicitly, or by
    monkeypatching run_adk_eval.sys.platform) regardless of whatever OS
    actually runs them."""
    if args.host_ollama:
        return True
    if args.docker_ollama:
        return False
    return (platform or sys.platform) == "darwin"


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


def compose_setup(ci: bool, host_ollama: bool = False) -> tuple:
    """(compose_args, extra_env) for this run.

    Deliberately does NOT use lib_docker.compose_file_args() (which follows
    whatever provider/model this developer's own .env/setup_llm.py last
    configured for the regular dev stack) - a real run once broke outright
    because that shared config had drifted out of sync with this developer's
    own OLLAMA_MODEL. Each mode always targets the same fixed, dedicated
    compose file + LiteLLM config, regardless of the calling machine's own
    setup: local (default) docker-compose.local.yaml + LOCAL_LITELLM_CONFIG/
    LOCAL_OLLAMA_MODEL (dockerized Ollama); --host-ollama
    docker-compose.local-hostollama.yaml + HOST_OLLAMA_LITELLM_CONFIG (native
    Ollama on this host - see ensure_host_ollama_ready, which handles the
    model itself rather than an env override here); --ci the cloud
    docker-compose.yaml (no Ollama) + CI_LITELLM_CONFIG.

    extra_env overrides LITELLM_CONFIG_PATH (see the parameterized litellm
    volume mount in all three compose files) and, in local (dockerized-
    Ollama) mode only, OLLAMA_MODEL too, so the `ollama` container actually
    pulls the same model this run's LiteLLM config expects.
    """
    if ci:
        return (
            lib_docker.compose_project_args("eval"),
            {"LITELLM_CONFIG_PATH": CI_LITELLM_CONFIG},
        )
    if host_ollama:
        return (
            ["-f", "docker-compose.local-hostollama.yaml"] + lib_docker.compose_project_args("eval"),
            {"LITELLM_CONFIG_PATH": HOST_OLLAMA_LITELLM_CONFIG},
        )
    return (
        ["-f", "docker-compose.local.yaml"] + lib_docker.compose_project_args("eval"),
        {"LITELLM_CONFIG_PATH": LOCAL_LITELLM_CONFIG, "OLLAMA_MODEL": LOCAL_OLLAMA_MODEL},
    )


def wait_for_litellm_ready(timeout_seconds: int = LITELLM_READY_TIMEOUT_SECONDS) -> bool:
    """Polls LiteLLM's own /health/readiness (published on localhost:4000 in
    both compose files) - mirrors eval.yml's existing "Start dependency
    services" polling loop for the team-performance harness. `up -d`
    returning success only means the container process started, not that
    LiteLLM has finished its own DB connection setup. Returns False on
    timeout rather than raising, so the caller can decide how to fail."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(LITELLM_HEALTH_URL, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    return False


def wait_for_ollama_model(compose_args: list, env_file: str, run_env: dict, model: str,
                           timeout_seconds: int = OLLAMA_MODEL_PULL_TIMEOUT_SECONDS) -> bool:
    """Polls `docker compose exec ollama ollama list` until `model` shows up.

    A real eval run kept failing with "model 'llama3.1:8b' not found" even
    after the model config and OLLAMA_MODEL were made consistent - the root
    cause was a race, not a mismatch: ollama-entrypoint.sh backgrounds
    `ollama serve` (so the container accepts connections, and `docker
    compose up -d` returns success) and only pulls the model as a separate
    step *afterward*, which can take several minutes on a first run. Every
    request sent before that pull finishes fails with this exact error,
    indistinguishable from a genuine connection problem - in that real run,
    most of the 10 eval cases ran (and failed) during the pull window, and
    only the last one or two succeeded once it finished partway through.

    No host port is published for `ollama` (see docker-compose.local.yaml -
    "nothing needs to reach it from the host"), so this execs into the
    container directly via Compose rather than hitting its API from the
    host. Returns False on timeout rather than raising."""
    deadline = time.monotonic() + timeout_seconds
    list_cmd = ["docker", "compose", *compose_args, "--env-file", env_file, "exec", "-T", "ollama", "ollama", "list"]
    printed_waiting = False
    while time.monotonic() < deadline:
        result = subprocess.run(list_cmd, env=run_env, capture_output=True, text=True)
        if result.returncode == 0 and model in (result.stdout or ""):
            return True
        if not printed_waiting:
            print(f"Waiting for Ollama to finish pulling {model} (first run only, cached afterwards)...")
            printed_waiting = True
        time.sleep(5)
    return False


def ensure_host_ollama_ready(model: str) -> bool:
    """Preflight for --host-ollama: the `ollama` CLI must be on PATH, a
    native Ollama instance must already be reachable (the user is expected
    to have run `ollama serve` beforehand - same prerequisite documented in
    docker-compose.local-hostollama.yaml), and the pinned model must be
    present, pulling it here if not.

    Unlike the dockerized case (wait_for_ollama_model), this runs entirely
    on the host, before any docker compose command - there's no race to
    poll for here, since nothing else starts until this function returns; a
    synchronous `ollama pull` can't race with anything. Prints its own
    error message and returns False on any failure; never raises."""
    if shutil.which("ollama") is None:
        print("ERROR: 'ollama' command not found on this host. Install it: https://ollama.com/download")
        return False
    if not lib_docker.host_ollama_reachable():
        print("ERROR: Ollama isn't reachable at http://localhost:11434 - start it first: ollama serve")
        return False
    list_result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
    if list_result.returncode == 0 and model in (list_result.stdout or ""):
        return True
    print(f"Pulling {model} on the host's native Ollama (first run only, cached by Ollama afterwards)...")
    pull_result = subprocess.run(["ollama", "pull", model])
    if pull_result.returncode != 0:
        print(f"ERROR: 'ollama pull {model}' failed.")
        return False
    return True


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

    # --ci always means the cloud stack, no Ollama at all - never resolved
    # via platform detection (a macOS CI runner shouldn't suddenly need a
    # native `ollama serve` prerequisite just because it's on macOS).
    host_ollama = False if args.ci else resolve_host_ollama(args)
    if host_ollama and not args.host_ollama and not args.ci:
        # Auto-detected, not explicitly requested - say so, since it comes
        # with a real prerequisite (a native `ollama serve` already running)
        # the dockerized default doesn't have.
        print("Detected macOS - defaulting to a native Ollama on this host for GPU acceleration "
              "(pass --docker-ollama to use the dockerized Ollama instead).")

    # GH issue #169: its own Compose project name (horseless-carriage-eval),
    # distinct from the dev stack's (run.py) and the test suite's
    # (run_tests.py) - see lib_docker.compose_project_args.
    compose_args, extra_env = compose_setup(args.ci, host_ollama)
    adk_cmd = adk_eval_command()
    run_env = {**os.environ, **extra_env}
    env_prefix = " ".join(f"{k}={v}" for k, v in extra_env.items())

    up_cmd = ["docker", "compose", *compose_args, "--env-file", args.env_file, "up", "-d", "db", "litellm"]
    run_cmd = ["docker", "compose", *compose_args, "--env-file", args.env_file, "run", "--rm"]
    if args.debug:
        # Opt-in, not forced - this eval set exists to catch gate-enforcement
        # regressions against a live model, so the full request/response
        # trace matters when actually diagnosing a failure, but it floods
        # the shell on every routine run otherwise (only pass --debug when
        # you actually need it).
        run_cmd += ["-e", "LOG_LEVEL=debug"]
    run_cmd += ["--entrypoint", "", "agent", *adk_cmd]
    down_cmd = ["docker", "compose", *compose_args, "--env-file", args.env_file, "down"]
    if args.ci:
        # Ephemeral CI runner - also drop named volumes, matching eval.yml's
        # own `down -v`. Kept locally (no -v) so the pinned Ollama model
        # doesn't need re-pulling on every subsequent local run.
        down_cmd.append("-v")

    if args.dry_run:
        print("Would run:")
        if host_ollama:
            print(f"  ensure the `ollama` CLI is present, Ollama is reachable at http://localhost:11434, and {LOCAL_OLLAMA_MODEL} is pulled (all on this host)")
        print(f"  {env_prefix} docker compose {' '.join(compose_args)} --env-file {args.env_file} up -d db litellm")
        print(f"  wait for {LITELLM_HEALTH_URL} (up to {LITELLM_READY_TIMEOUT_SECONDS}s)")
        if not args.ci and not host_ollama:
            print(f"  wait for `ollama list` to show {LOCAL_OLLAMA_MODEL} (up to {OLLAMA_MODEL_PULL_TIMEOUT_SECONDS}s - first pull only, cached afterwards)")
        print(f"  {' '.join(run_cmd)}")
        print(f"  {' '.join(down_cmd)}")
        return

    # Host-Ollama's own readiness/pull check runs entirely on the host,
    # before any docker compose command - see ensure_host_ollama_ready's
    # docstring for why this can't race the way the dockerized case can.
    if host_ollama and not ensure_host_ollama_ready(LOCAL_OLLAMA_MODEL):
        sys.exit(1)

    exit_code = 1
    try:
        print("--- Bringing up db + litellm ---")
        result = subprocess.run(up_cmd, env=run_env)
        if result.returncode != 0:
            exit_code = result.returncode
        elif not wait_for_litellm_ready():
            print(f"ERROR: litellm did not report ready at {LITELLM_HEALTH_URL} within {LITELLM_READY_TIMEOUT_SECONDS}s.")
            exit_code = 1
        elif not args.ci and not host_ollama and not wait_for_ollama_model(compose_args, args.env_file, run_env, LOCAL_OLLAMA_MODEL):
            # --ci uses a cloud model, host-ollama already ensured the
            # model above - neither has a dockerized `ollama` pull to wait for.
            print(f"ERROR: Ollama did not finish pulling {LOCAL_OLLAMA_MODEL} within {OLLAMA_MODEL_PULL_TIMEOUT_SECONDS}s.")
            exit_code = 1
        else:
            print(f"--- Running ADK eval set: {EVAL_SET_PATH} ---")
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
