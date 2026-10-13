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
