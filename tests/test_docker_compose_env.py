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


def _agent_service_env_entries(compose_path: Path) -> dict:
    """name -> raw value expression (e.g. "${FOO:-bar}"), for entries that
    reference a Compose variable at all - literal/hardcoded values (e.g.
    INTERNAL_STATE_REPO_PATH=/app/state_repo) are skipped."""
    data = yaml.safe_load((REPO_ROOT / compose_path).read_text(encoding="utf-8"))
    entries = data["services"]["agent"]["environment"]
    result = {}
    for entry in entries:
        key, _, value = entry.partition("=")
        if "${" in value:
            result[key] = value
    return result


# GH issue #82: these vars all have an unconditional, safe fallback in the
# Python code that actually reads them (get_interaction_level(),
# _default_push_branch(), notifications.get_configured_notifiers(), etc.) -
# an inline `${VAR:-default}` here (matching that same code-side default)
# means Compose's own "variable is not set" warning only ever fires for a
# var that's genuinely required with no safe fallback, not for one the app
# was always going to default gracefully anyway.
_VARS_EXPECTED_TO_HAVE_INLINE_COMPOSE_DEFAULTS = {
    "GITHUB_REPO_BRANCH",
    "SPRINT_TOKEN_BUDGET",
    "PROCESS_OVERHEAD_PERCENTAGE",
    "SESSION_ID",
    "INTERACTION_LEVEL",
    "GITHUB_DEVELOP_BRANCH",
    "NOTIFICATION_PLUGINS",
    "TRANSCRIPT_MAX_ENTRIES",
    # Deprecated-name pairs (GH issue #81) - these get an *empty* `:-`
    # default rather than a real one: get_env_with_deprecated_fallback needs
    # to see each one genuinely absent (falsy) to know to check the other, so
    # a real default here would silently mask the deprecated name. Still
    # satisfies this test (":-" is present), just not a "real" default value.
    "TOTAL_USD_BUDGET",
    "SPRINT_USD_BUDGET",
    "EVAL_USD_BUDGET_PER_SPRINT",
    "EVAL_SPRINT_USD_BUDGET",
}


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


class TestNoSpuriousComposeWarningsForVarsWithSafeDefaults:
    """
    GH issue #82: "Warnings about not set environment variables" - Compose
    warned about NOTIFICATION_PLUGINS/TRANSCRIPT_MAX_ENTRIES being unset even
    though the code reading them already defaults gracefully. Every var in
    _VARS_EXPECTED_TO_HAVE_INLINE_COMPOSE_DEFAULTS must use `${VAR:-default}`
    (not bare `${VAR}`) in both compose files' agent environment, so Compose
    itself never has anything to warn about for these.
    """

    def test_docker_compose_yaml_has_inline_defaults(self):
        entries = _agent_service_env_entries(Path("docker-compose.yaml"))
        self._assert_all_have_defaults(entries)

    def test_docker_compose_local_yaml_has_inline_defaults(self):
        entries = _agent_service_env_entries(Path("docker-compose.local.yaml"))
        self._assert_all_have_defaults(entries)

    def _assert_all_have_defaults(self, entries):
        missing = [
            name for name in _VARS_EXPECTED_TO_HAVE_INLINE_COMPOSE_DEFAULTS
            if name in entries and ":-" not in entries[name]
        ]
        assert not missing, (
            f"{missing} are referenced as bare ${{VAR}} (no inline default) despite the code that "
            "reads them already defaulting gracefully - use ${VAR:-default} instead (GH issue #82)."
        )
