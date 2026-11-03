"""Tests for dashboard.py (GH issue #310)."""
import json
from pathlib import Path
from unittest.mock import patch

import dashboard
import project_registry


def _make_project(tmp_path, name="my-project"):
    hc_path = tmp_path / name / "horseless-carriage"
    hc_path.mkdir(parents=True)
    (hc_path / ".env").write_text("")
    target_repo = tmp_path / name
    return hc_path, target_repo


class TestProjectSnapshot:
    def test_missing_hc_path_is_reported_without_raising(self, tmp_path):
        entry = {"name": "gone", "hc_path": str(tmp_path / "nowhere"), "target_repo": str(tmp_path)}
        snap = dashboard.project_snapshot(entry)
        assert snap["state"] == "missing"
        assert "no longer exists" in snap["error"]

    def test_resting_when_stack_is_not_running(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["state"] == "resting"
        assert snap["error"] is None

    def test_working_when_running_with_no_blocking_interactions(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({
            "sprint_goal": "Ship the thing",
            "blocking_interactions": [],
            "budgets": {"total": 1000000, "total_usd": 5.0},
            "token_usage": {"total": 250000},
        }))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]):
            snap = dashboard.project_snapshot(entry)

        assert snap["state"] == "working"
        assert snap["sprint_goal"] == "Ship the thing"
        assert snap["token_usage"] == 250000
        assert snap["token_budget"] == 1000000
        assert snap["usd_budget"] == 5.0

    def test_reads_current_usd_spend_when_present(self, tmp_path):
        """GH issue #481: already computed/persisted by the live agent
        session's own cost-budget callback - the dashboard previously never
        surfaced it, only the budget ceiling."""
        hc_path, target_repo = _make_project(tmp_path)
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({
            "budgets": {"total_usd": 5.0, "current_usd_spend": 1.23},
        }))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["current_usd_spend"] == 1.23

    def test_current_usd_spend_is_none_when_not_yet_computed(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({
            "budgets": {"total_usd": 5.0},
        }))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["current_usd_spend"] is None

    def test_waiting_when_running_with_blocking_interactions(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({
            "blocking_interactions": [{"question": "which framework?"}],
        }))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]):
            snap = dashboard.project_snapshot(entry)

        assert snap["state"] == "waiting for confirmation"

    def test_blocking_summary_surfaces_the_most_recent_unresolved_interaction(self, tmp_path):
        """GH issue #454: the dashboard gave no clue what a 'waiting for
        confirmation' project was actually waiting on."""
        hc_path, target_repo = _make_project(tmp_path)
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({
            "blocking_interactions": [
                {"summary": "Older, already resolved", "resolved": True},
                {"summary": "Approve release PR #42"},
            ],
        }))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]):
            snap = dashboard.project_snapshot(entry)

        assert snap["state"] == "waiting for confirmation"
        assert snap["blocking_summary"] == "Approve release PR #42"
        assert snap["blocking_count"] == 1

    def test_resolved_interactions_dont_count_as_blocking(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({
            "blocking_interactions": [{"summary": "Done already", "resolved": True}],
        }))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]):
            snap = dashboard.project_snapshot(entry)

        assert snap["state"] == "working"
        assert snap["blocking_count"] == 0

    def test_missing_state_json_does_not_raise(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["state"] == "resting"
        assert snap["sprint_goal"] == "(not set)"

    def test_reads_interaction_level_from_env(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        (hc_path / ".env").write_text("INTERACTION_LEVEL='CEO'\n")
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["interaction_level"] == "CEO"

    def test_defaults_interaction_level_to_product_when_unset(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["interaction_level"] == "Product"

    def test_defaults_ports_when_unset(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["litellm_port"] == "4000"
        assert snap["agent_web_port"] == "8000"

    def test_reads_configured_ports_from_env(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        (hc_path / ".env").write_text("LITELLM_HOST_PORT='4001'\nAGENT_WEB_HOST_PORT='8001'\n")
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["litellm_port"] == "4001"
        assert snap["agent_web_port"] == "8001"

    def test_uses_registry_target_repo_when_env_has_no_state_repo_path(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["target_repo"] == str(target_repo)
        assert snap["target_repo_drifted"] is False

    def test_prefers_the_live_env_state_repo_path_over_the_registry_snapshot(self, tmp_path):
        """GH issue tracker: a real incident - two different projects'
        dashboard rows showed each other's sprint goal because one's .env
        STATE_REPO_PATH (carried over from copying an .env between two
        projects' submodule checkouts) still pointed at the other's
        directory, while project_registry.json's own target_repo (set once
        at install time) correctly still said this project's own path.
        Trusting the registry's install-time snapshot over the live .env
        the actual running agent uses was the bug - the live value must win."""
        hc_path, target_repo = _make_project(tmp_path)
        other_project_repo = tmp_path / "other-project"
        other_project_repo.mkdir()
        (other_project_repo / ".hc").mkdir()
        (other_project_repo / ".hc" / "state.json").write_text(json.dumps({"sprint_goal": "Other project's goal"}))
        (hc_path / ".env").write_text(f"STATE_REPO_PATH='{other_project_repo}'\n")
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["target_repo"] == str(other_project_repo)
        assert snap["sprint_goal"] == "Other project's goal"
        assert snap["target_repo_drifted"] is True

    def test_relative_state_repo_path_resolves_against_hc_path_not_cwd(self, tmp_path):
        """STATE_REPO_PATH may be a relative path (.env.example's own
        default is "../Horseless-Carriage-State") - Docker Compose resolves
        it against the compose file's own directory (hc_path), and this
        must match that exactly or every relative-path setup would falsely
        flag as drifted."""
        hc_path, target_repo = _make_project(tmp_path)
        # hc_path is target_repo/horseless-carriage, so ".." from there is
        # target_repo itself.
        (hc_path / ".env").write_text("STATE_REPO_PATH='..'\n")
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["target_repo_drifted"] is False


class TestProductVersion:
    def test_reads_version_file_if_present(self, tmp_path):
        (tmp_path / "VERSION").write_text("1.2.3\n")
        assert dashboard._product_version(tmp_path) == "1.2.3"

    def test_falls_back_to_unknown_with_no_version_file_or_git(self, tmp_path):
        assert dashboard._product_version(tmp_path) == "unknown"


class TestRenderPage:
    def test_renders_no_projects_message_when_registry_is_empty(self):
        with patch.object(project_registry, "load", return_value=[]):
            page = dashboard._render_page()
        assert "No projects registered yet" in page

    def test_renders_a_registered_project_row(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            page = dashboard._render_page()

        assert "my-project" in page
        assert "resting" in page

    def test_renders_current_usd_spend_alongside_the_budget(self, tmp_path):
        """GH issue #481: only the budget ceiling was ever shown - the
        actual cumulative spend (already computed by the live agent
        session) must be visible too."""
        hc_path, target_repo = _make_project(tmp_path)
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({
            "budgets": {"total_usd": 5.0, "current_usd_spend": 1.23},
        }))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            page = dashboard._render_page()

        assert "$1.23 spent" in page
        assert "$5.00" in page

    def test_renders_a_warning_when_target_repo_has_drifted(self, tmp_path):
        """A real incident: one project's dashboard row silently showed
        another project's sprint goal because its .env STATE_REPO_PATH had
        been carried over from the other project - this must be visible,
        not silent."""
        hc_path, target_repo = _make_project(tmp_path)
        other_project_repo = tmp_path / "other-project"
        other_project_repo.mkdir()
        (hc_path / ".env").write_text(f"STATE_REPO_PATH='{other_project_repo}'\n")
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            page = dashboard._render_page()

        assert "differs from this project's registered path" in page
        assert str(other_project_repo) in page

    def test_no_drift_warning_when_env_matches_the_registry(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            page = dashboard._render_page()

        assert "differs from this project's registered path" not in page

    def test_renders_spend_not_yet_available_when_absent(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({
            "budgets": {"total_usd": 5.0},
        }))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            page = dashboard._render_page()

        assert "spend not yet available" in page

    def test_escapes_html_in_sprint_goal(self, tmp_path):
        """A sprint_goal is LLM-authored, agent-controlled text - must not
        be interpreted as HTML/script when rendered."""
        hc_path, target_repo = _make_project(tmp_path)
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({
            "sprint_goal": "<script>alert(1)</script>",
        }))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            page = dashboard._render_page()

        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page

    def test_renders_the_projects_assigned_ports(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        (hc_path / ".env").write_text("LITELLM_HOST_PORT='4001'\nAGENT_WEB_HOST_PORT='8001'\n")
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            page = dashboard._render_page()

        assert "4001" in page
        assert "8001" in page
        assert "http://localhost:4001/ui" in page
        assert "http://localhost:8001" in page

    def test_renders_blocking_summary_and_web_ui_link_when_waiting(self, tmp_path):
        """GH issue #454: starting a project that comes up already blocked
        on a human must point at where to go resolve it, not just say
        'waiting for confirmation' with no next step."""
        hc_path, target_repo = _make_project(tmp_path)
        (hc_path / ".env").write_text("AGENT_WEB_HOST_PORT='8001'\n")
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({
            "blocking_interactions": [{"summary": "Approve release PR #42"}],
        }))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]):
            page = dashboard._render_page()

        assert "Approve release PR #42" in page
        assert "http://localhost:8001" in page
        assert "Respond in web UI" in page


class TestStartStack:
    def test_no_build_flag_when_ports_are_unchanged(self, tmp_path):
        hc_path, _ = _make_project(tmp_path)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return dashboard.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch.object(dashboard.lib_docker, "ensure_dev_ports_available", return_value=[]), \
             patch.object(dashboard.subprocess, "run", side_effect=fake_run), \
             patch.object(dashboard.lib_llm_test, "llm_wait_for_proxy", return_value=True):
            message = dashboard._start_stack(hc_path)

        assert "--build" not in calls[0]
        assert message == "Started."

    def test_build_flag_added_and_change_reported_when_a_port_was_reassigned(self, tmp_path):
        hc_path, _ = _make_project(tmp_path)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return dashboard.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch.object(dashboard.lib_docker, "ensure_dev_ports_available",
                           return_value=[("LITELLM_HOST_PORT", 4000, 4001)]), \
             patch.object(dashboard.subprocess, "run", side_effect=fake_run), \
             patch.object(dashboard.lib_llm_test, "llm_wait_for_proxy", return_value=True):
            message = dashboard._start_stack(hc_path)

        assert "--build" in calls[0]
        assert "LITELLM_HOST_PORT 4000 -> 4001" in message

    def test_warns_when_litellm_does_not_become_ready(self, tmp_path):
        """GH issue tracker: `docker compose up -d` only waits for the
        container to be *running*, not for LiteLLM's own process inside it
        to finish booting - a user clicking this dashboard's own LiteLLM
        link right after "Started" previously had no way to know it might
        still be starting."""
        hc_path, _ = _make_project(tmp_path)

        with patch.object(dashboard.lib_docker, "ensure_dev_ports_available", return_value=[]), \
             patch.object(dashboard.subprocess, "run",
                           return_value=dashboard.subprocess.CompletedProcess([], 0, stdout="", stderr="")), \
             patch.object(dashboard.lib_llm_test, "llm_wait_for_proxy", return_value=False) as mock_wait:
            message = dashboard._start_stack(hc_path)

        assert "Started." in message
        assert "didn't report ready" in message
        mock_wait.assert_called_once_with("http://localhost:4000", timeout_secs=30)

    def test_waits_for_the_reassigned_port_when_one_changed(self, tmp_path):
        """Must wait on the port actually configured in .env at the time
        `docker compose up -d` returns (i.e. after ensure_dev_ports_available's
        own real .env write), not a stale default."""
        hc_path, _ = _make_project(tmp_path)

        def fake_reassign(hc_path_arg, compose_args):
            dashboard.lib_env.update_env_var(hc_path_arg / ".env", "LITELLM_HOST_PORT", "4001")
            return [("LITELLM_HOST_PORT", 4000, 4001)]

        with patch.object(dashboard.lib_docker, "ensure_dev_ports_available", side_effect=fake_reassign), \
             patch.object(dashboard.subprocess, "run",
                           return_value=dashboard.subprocess.CompletedProcess([], 0, stdout="", stderr="")), \
             patch.object(dashboard.lib_llm_test, "llm_wait_for_proxy", return_value=True) as mock_wait:
            dashboard._start_stack(hc_path)

        mock_wait.assert_called_once_with("http://localhost:4001", timeout_secs=30)

    def test_start_failure_is_reported_even_with_a_port_change(self, tmp_path):
        hc_path, _ = _make_project(tmp_path)

        with patch.object(dashboard.lib_docker, "ensure_dev_ports_available",
                           return_value=[("LITELLM_HOST_PORT", 4000, 4001)]), \
             patch.object(dashboard.subprocess, "run",
                           return_value=dashboard.subprocess.CompletedProcess([], 1, stdout="out", stderr="err")):
            message = dashboard._start_stack(hc_path)

        assert "Start failed" in message
