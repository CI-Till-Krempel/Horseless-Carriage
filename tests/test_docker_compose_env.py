"""
Regression test for GH issue #76: docker-compose.yaml/docker-compose.local.
yaml's `agent` service `environment:` list is an explicit whitelist -
Docker Compose does NOT forward the rest of the host's .env into the
container just because a var is set there. INTERACTION_LEVEL (among
others) was read by agents/scrum_team code via os.getenv but never listed
in either compose file, so it silently always read back as unset/default
regardless of what was actually configured.

This scans agents/scrum_team/ (and auth_github.py, which the container
also runs) for every os.getenv/os.environ.get("SOME_VAR") call, and
asserts each one (except a documented, deliberate allowlist of vars that
are either container-internal or CI/eval-context-only, never meant to come
from a user's .env at all) is present in both compose files' `agent`
service environment list - so a *future* new env var read in code and
forgotten in the compose files fails this test too, not just this one.
"""
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

# Deliberately not expected to be passed via docker-compose's agent service:
# - INTERNAL_STATE_REPO_PATH: a docker-internal-only override, already
#   hardcoded to /app/state_repo in both compose files, not sourced from
#   the host's own environment/.env at all.
# - GITHUB_ACTIONS/GITHUB_REPOSITORY/GITHUB_RUN_ID/GITHUB_SERVER_URL: set
#   automatically by the GitHub Actions runner itself when running inside
#   a workflow (agents/scrum_team/scripts/_eval_git_utils.py) - never a
#   user's own .env value, and not applicable when running via
#   docker-compose at all.
# - EVAL_RUN_ID: the eval harness's own internal run-isolation id, set by
#   run_eval.py itself for its own subprocess, not read from the host .env.
_NOT_EXPECTED_FROM_COMPOSE = {
    "INTERNAL_STATE_REPO_PATH",
    "GITHUB_ACTIONS",
    "GITHUB_REPOSITORY",
    "GITHUB_RUN_ID",
    "GITHUB_SERVER_URL",
    "EVAL_RUN_ID",
}

_ENV_VAR_READ_PATTERN = re.compile(r'os\.(?:getenv|environ\.get)\(\s*["\']([A-Z][A-Z0-9_]*)["\']')


def _env_vars_read_by_agent_code() -> set:
    found = set()
    paths = list((REPO_ROOT / "agents" / "scrum_team").rglob("*.py")) + [REPO_ROOT / "auth_github.py"]
    for path in paths:
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        found.update(_ENV_VAR_READ_PATTERN.findall(text))
    return found


def _agent_service_env_var_names(compose_path: Path) -> set:
    data = yaml.safe_load((REPO_ROOT / compose_path).read_text(encoding="utf-8"))
    entries = data["services"]["agent"]["environment"]
    names = set()
    for entry in entries:
        key = entry.split("=", 1)[0]
        names.add(key)
    return names


class TestAgentContainerEnvironmentCompleteness:
    def test_every_env_var_the_agent_code_reads_is_passed_via_docker_compose_yaml(self):
        expected = _env_vars_read_by_agent_code() - _NOT_EXPECTED_FROM_COMPOSE
        actual = _agent_service_env_var_names(Path("docker-compose.yaml"))
        missing = expected - actual
        assert not missing, (
            f"docker-compose.yaml's agent service environment: list is missing {sorted(missing)} - "
            "the agent code reads these via os.getenv, but Compose only forwards vars explicitly "
            "listed here, so they'd always read back as unset regardless of .env (GH issue #76)."
        )

    def test_every_env_var_the_agent_code_reads_is_passed_via_docker_compose_local_yaml(self):
        expected = _env_vars_read_by_agent_code() - _NOT_EXPECTED_FROM_COMPOSE
        actual = _agent_service_env_var_names(Path("docker-compose.local.yaml"))
        missing = expected - actual
        assert not missing, (
            f"docker-compose.local.yaml's agent service environment: list is missing {sorted(missing)} - "
            "the agent code reads these via os.getenv, but Compose only forwards vars explicitly "
            "listed here, so they'd always read back as unset regardless of .env (GH issue #76)."
        )

    def test_interaction_level_specifically_is_passed_through(self):
        """The exact symptom reported in GH issue #76: INTERACTION_LEVEL=
        Stakeholder in .env was read back as "Product" inside the running
        agent - because this exact variable was missing from both compose
        files' agent environment list."""
        for compose_file in ("docker-compose.yaml", "docker-compose.local.yaml"):
            assert "INTERACTION_LEVEL" in _agent_service_env_var_names(Path(compose_file)), compose_file
