"""Tests for dashboard.py (GH issue #310)."""
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import dashboard
import doctor
import project_registry


@pytest.fixture(autouse=True)
def _stub_doctor_check(monkeypatch):
    """doctor.check() does real subprocess calls (docker, gh) and - without
    dry_run - real filesystem writes; project_snapshot now calls it
    unconditionally for every project on every render. Every test not
    specifically about that integration gets a fast, deterministic "no
    issues" stub instead - tests exercising the real wiring (TestDoctorItems
    below) override this again within their own body."""
    monkeypatch.setattr(dashboard.doctor, "check", lambda *a, **k: doctor.DoctorResult(items=[]))


@pytest.fixture(autouse=True)
def _clear_release_cache():
    """_cached_latest_release's module-level cache is keyed only on
    (owner, repo) - without this, an earlier test's mocked GitHub release
    for "example/example" would leak into a later test reusing that same
    owner/repo, independent of execution order."""
    dashboard._release_cache.clear()


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

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]), \
             patch.object(dashboard.lib_docker, "running_state_repo_mount", return_value=None):
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

    def test_surfaces_sprint_phase_and_current_ticket(self, tmp_path):
        """A coarser, always-on substitute for the fine-grained
        blocking_summary text (which only ever says anything while a
        specific interaction is unresolved) - the badge word alone doesn't
        say what the team is actually doing right now."""
        hc_path, target_repo = _make_project(tmp_path)
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({
            "sprint_number": 1,
            "sprint_backlog_pr_sprint": 1,
            "sprint_backlog": [{"id": "US-0001", "type": "User Story", "stages_completed": []}],
            "product_backlog": [{"id": "US-0001", "type": "User Story", "title": "Add login", "stages_completed": []}],
        }))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]), \
             patch.object(dashboard.lib_docker, "running_state_repo_mount", return_value=None):
            snap = dashboard.project_snapshot(entry)

        assert snap["sprint_phase"] == "Development"
        assert snap["current_ticket"] == "US-0001: Add login"

    def test_sprint_phase_and_current_ticket_are_blank_with_no_state_data(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["sprint_phase"] == ""
        assert snap["current_ticket"] == ""

    def test_waiting_when_running_with_blocking_interactions(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({
            "blocking_interactions": [{"question": "which framework?"}],
        }))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]), \
             patch.object(dashboard.lib_docker, "running_state_repo_mount", return_value=None):
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

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]), \
             patch.object(dashboard.lib_docker, "running_state_repo_mount", return_value=None):
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

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]), \
             patch.object(dashboard.lib_docker, "running_state_repo_mount", return_value=None):
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

    def test_drift_detection_self_heals_the_registry_file(self, tmp_path):
        """Not just a rendered warning - project_registry.json itself must
        actually get corrected, so the drift doesn't keep recurring on
        every render and other tools reading the registry (not just this
        dashboard) see the right value too."""
        target_repo = tmp_path / "actual-target"
        target_repo.mkdir()
        git_parent = tmp_path / "registered-name"
        git_parent.mkdir(exist_ok=True)
        (git_parent / ".git").mkdir()
        nested_hc_path = git_parent / "horseless-carriage"
        nested_hc_path.mkdir()
        (nested_hc_path / ".env").write_text(f"STATE_REPO_PATH='{target_repo}'\n")
        entry = {"name": "registered-name", "hc_path": str(nested_hc_path), "target_repo": str(git_parent)}
        registry_path = tmp_path / "registry.json"

        with patch.object(dashboard.project_registry, "REGISTRY_PATH", registry_path), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            dashboard.project_snapshot(entry)
            projects = dashboard.project_registry.load()

        assert len(projects) == 1
        assert projects[0]["target_repo"] == str(target_repo)

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

    def test_resting_project_is_not_probed_for_reachability(self, tmp_path):
        """A stopped project is expected to be unreachable - probing it
        would just add latency (and its own timeout) to every page load for
        no useful signal."""
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]), \
             patch.object(dashboard, "_url_reachable") as mock_reachable:
            snap = dashboard.project_snapshot(entry)

        mock_reachable.assert_not_called()
        assert snap["litellm_reachable"] is False
        assert snap["web_reachable"] is False

    def test_running_project_reports_reachability_per_link(self, tmp_path):
        """GH issue tracker: a project shown as 'working' (its containers are
        up) could still have a LiteLLM/web UI link that 404s/refuses to
        connect if the service inside hasn't finished booting (or crashed
        after) - the container being 'running' says nothing about that."""
        hc_path, target_repo = _make_project(tmp_path)
        (hc_path / ".env").write_text("LITELLM_HOST_PORT='4001'\nAGENT_WEB_HOST_PORT='8001'\n")
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        def fake_reachable(url, timeout_secs=dashboard._REACHABILITY_CHECK_TIMEOUT_SECS):
            return "4001" in url

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]), \
             patch.object(dashboard.lib_docker, "running_state_repo_mount", return_value=None), \
             patch.object(dashboard, "_url_reachable", side_effect=fake_reachable):
            snap = dashboard.project_snapshot(entry)

        assert snap["litellm_reachable"] is True
        assert snap["web_reachable"] is False

    def test_reads_hc_version_from_state_json(self, tmp_path):
        """RELEASE.md's "Tracking the HC version in the state repo":
        agents/scrum_team/tools/scrum.py's init_scrum_state sets this on
        every session to the Horseless Carriage checkout's own VERSION -
        distinct from product_version (the TARGET repo's own version)."""
        hc_path, target_repo = _make_project(tmp_path)
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({"hc_version": "0.2.0"}))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["hc_version"] == "0.2.0"

    def test_hc_commit_reads_short_hash_from_hc_paths_own_git_checkout(self, tmp_path):
        """dashboard.py runs on the host, where hc_path's .git is directly
        available (unlike hc_version, recorded from *inside* the agent
        container, whose image deliberately excludes .git - see
        _git_short_commit's docstring) - so it's read straight from there, in
        addition to (not instead of) hc_version, for display as
        "1.0.0 (e83912ed)"."""
        hc_path, target_repo = _make_project(tmp_path)
        subprocess.run(["git", "init"], cwd=hc_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=hc_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=hc_path, check=True)
        (hc_path / "VERSION").write_text("1.0.0\n")
        subprocess.run(["git", "add", "VERSION"], cwd=hc_path, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=hc_path, check=True, capture_output=True)
        expected = subprocess.run(
            ["git", "-C", str(hc_path), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["hc_commit"] == expected

    def test_hc_commit_is_none_when_hc_path_is_not_a_git_checkout(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["hc_commit"] is None

    def test_project_snapshot_uses_the_latest_github_release_for_product_version(self, tmp_path):
        """End-to-end: GITHUB_REPO_URL + GITHUB_TOKEN configured in this
        project's own hc_path/.env, a release already published for that
        repo - product_version/product_commit should come from the release,
        not the target repo's local VERSION file."""
        hc_path, target_repo = _make_project(tmp_path)
        (hc_path / ".env").write_text(
            "GITHUB_REPO_URL='git@github.com:example/example.git'\nGITHUB_TOKEN='tok123'\n"
        )
        (target_repo / "VERSION").write_text("0.9.0\n")
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]), \
             patch.object(dashboard.lib_github, "latest_release",
                           return_value={"tag_name": "v1.0.0", "commit_sha": "e83912ed1234567"}) as mock_release:
            snap = dashboard.project_snapshot(entry)

        assert snap["product_version"] == "1.0.0"
        assert snap["product_commit"] == "e83912ed"
        mock_release.assert_called_once_with("example", "example", "tok123")

    def test_hc_version_defaults_to_unknown_when_absent(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["hc_version"] == "unknown"

    def test_builds_the_github_prs_url_from_the_hc_installations_own_env(self, tmp_path):
        """GITHUB_REPO_URL lives in the HC installation's own .env (hc_path),
        not the target repo's - setup_llm.py/new_project.py write it there,
        and that's the repo the agent is actually configured to operate on."""
        hc_path, target_repo = _make_project(tmp_path)
        (hc_path / ".env").write_text("GITHUB_REPO_URL='git@github.com:example/example.git'\n")
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["github_prs_url"] == "https://github.com/example/example/pulls"

    def test_github_prs_url_is_none_when_repo_url_unset(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["github_prs_url"] is None

    def test_github_prs_url_is_none_for_an_unparseable_repo_url(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        (hc_path / ".env").write_text("GITHUB_REPO_URL='not-a-github-url'\n")
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["github_prs_url"] is None

    def test_reads_sprint_number_from_state_json(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({"sprint_number": 3}))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["sprint_number"] == 3

    def test_sprint_number_is_none_when_absent(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            snap = dashboard.project_snapshot(entry)

        assert snap["sprint_number"] is None

    def test_running_container_mount_drift_is_read_from_instead_of_configured_path(self, tmp_path):
        """GH issue tracker ("dashboard doesn't update" report): `docker
        compose up` only recreates a container when Compose notices the
        resolved config changed - a container started BEFORE STATE_REPO_PATH
        was repointed keeps running against its OLD bind mount. Trusting
        the newly-configured .env path here would read a directory the live
        agent never writes to at all - forever stale no matter how often
        this re-renders - instead of the container's real, still-updating
        mount."""
        hc_path, configured_target_repo = _make_project(tmp_path)
        actually_running_repo = tmp_path / "actually-running"
        (actually_running_repo / ".hc").mkdir(parents=True)
        (actually_running_repo / ".hc" / "state.json").write_text(json.dumps({
            "sprint_goal": "The real, currently-updating sprint goal",
        }))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(configured_target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]), \
             patch.object(dashboard.lib_docker, "running_state_repo_mount",
                           return_value=str(actually_running_repo)):
            snap = dashboard.project_snapshot(entry)

        assert snap["state_repo_mount_drifted"] is True
        assert snap["target_repo"] == str(actually_running_repo)
        assert snap["configured_target_repo"] == str(configured_target_repo)
        assert snap["sprint_goal"] == "The real, currently-updating sprint goal"

    def test_no_mount_drift_when_running_mount_matches_configured_path(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        target_repo.mkdir(exist_ok=True)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]), \
             patch.object(dashboard.lib_docker, "running_state_repo_mount", return_value=str(target_repo)):
            snap = dashboard.project_snapshot(entry)

        assert snap["state_repo_mount_drifted"] is False
        assert snap["target_repo"] == str(target_repo)

    def test_mount_drift_check_skipped_when_resting(self, tmp_path):
        """A resting project has no running container to ask Docker about -
        calling it anyway would just be a wasted subprocess."""
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]), \
             patch.object(dashboard.lib_docker, "running_state_repo_mount") as mock_mount:
            snap = dashboard.project_snapshot(entry)

        mock_mount.assert_not_called()
        assert snap["state_repo_mount_drifted"] is False

    def test_mount_drift_check_failing_does_not_raise(self, tmp_path):
        """lib_docker.running_state_repo_mount is itself already
        best-effort (returns None on any failure) - this guards the call
        site too, consistent with every other docker/git call in this
        function never taking the whole row down."""
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]), \
             patch.object(dashboard.lib_docker, "running_state_repo_mount", side_effect=Exception("boom")):
            snap = dashboard.project_snapshot(entry)

        assert snap["state_repo_mount_drifted"] is False
        assert snap["error"] is None


class TestDoctorItems:
    """GH issue tracker: the dashboard should surface doctor.py's own
    warnings/errors right on a project's row - this is the glue connecting
    project_snapshot to doctor.check(), called in dry-run (no writes) +
    skip_llm_probe (no live network) mode since it now runs on every 10s
    auto-refresh for every registered project."""

    def test_calls_check_with_dry_run_and_skip_llm_probe(self, tmp_path):
        calls = []

        def fake_check(repo_root, **kwargs):
            calls.append((repo_root, kwargs))
            return doctor.DoctorResult(items=[])

        with patch.object(dashboard.doctor, "check", side_effect=fake_check):
            dashboard._doctor_items(tmp_path)

        assert calls == [(tmp_path, {"skip_llm_probe": True, "dry_run": True})]

    def test_converts_items_to_plain_dicts(self, tmp_path):
        items = [doctor.ActionableItem("error", "bad"), doctor.ActionableItem("warning", "meh")]
        with patch.object(dashboard.doctor, "check", return_value=doctor.DoctorResult(items=items)):
            result = dashboard._doctor_items(tmp_path)

        assert result == [{"severity": "error", "message": "bad"}, {"severity": "warning", "message": "meh"}]

    def test_suppresses_doctor_checks_own_stdout_output(self, tmp_path, capsys):
        def fake_check(repo_root, **kwargs):
            print("this should not reach the dashboard server's own console")
            return doctor.DoctorResult(items=[])

        with patch.object(dashboard.doctor, "check", side_effect=fake_check):
            dashboard._doctor_items(tmp_path)

        assert capsys.readouterr().out == ""

    def test_a_failing_check_degrades_to_no_items_rather_than_raising(self, tmp_path):
        with patch.object(dashboard.doctor, "check", side_effect=Exception("boom")):
            assert dashboard._doctor_items(tmp_path) == []

    def test_project_snapshot_includes_doctor_items(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}
        items = [doctor.ActionableItem("warning", "GIT_USER_NAME is not set")]

        with patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]), \
             patch.object(dashboard.doctor, "check", return_value=doctor.DoctorResult(items=items)):
            snap = dashboard.project_snapshot(entry)

        assert snap["doctor_items"] == [{"severity": "warning", "message": "GIT_USER_NAME is not set"}]


class TestDoctorBadge:
    def test_ok_when_no_items(self):
        assert "OK" in dashboard._doctor_badge([])
        assert "warning" not in dashboard._doctor_badge([])

    def test_shows_warning_count(self):
        badge = dashboard._doctor_badge([{"severity": "warning", "message": "GIT_USER_NAME is not set"}])
        assert "1 warning(s)" in badge
        assert "GIT_USER_NAME is not set" in badge

    def test_shows_error_count_and_uses_error_color_when_both_present(self):
        badge = dashboard._doctor_badge([
            {"severity": "error", "message": "boom"},
            {"severity": "warning", "message": "meh"},
        ])
        assert "1 error(s)" in badge
        assert "1 warning(s)" in badge
        assert "#c62828" in badge

    def test_escapes_html_in_messages(self):
        badge = dashboard._doctor_badge([{"severity": "warning", "message": "<script>alert(1)</script>"}])
        assert "<script>alert(1)</script>" not in badge
        assert "&lt;script&gt;" in badge


class TestProductVersion:
    def test_reads_version_file_if_present(self, tmp_path):
        (tmp_path / "VERSION").write_text("1.2.3\n")
        assert dashboard._product_version(tmp_path) == "1.2.3"

    def test_falls_back_to_unknown_with_no_version_file_or_git(self, tmp_path):
        assert dashboard._product_version(tmp_path) == "unknown"


class TestProductVersionAndCommit:
    def test_prefers_the_latest_github_release_over_the_local_version_file(self, tmp_path):
        """If there's already been a release, that's what actually shipped -
        the local checkout (VERSION file/HEAD) can be ahead of it with
        mid-sprint commits not yet released."""
        (tmp_path / "VERSION").write_text("0.9.0\n")
        with patch.object(dashboard, "_cached_latest_release",
                           return_value={"tag_name": "v1.0.0", "commit_sha": "e83912ed1234567"}):
            version, commit = dashboard._product_version_and_commit(tmp_path, ("example", "example"), {})

        assert version == "1.0.0"  # leading "v" stripped to match the VERSION-file style
        assert commit == "e83912ed"  # truncated to 8 chars

    def test_falls_back_to_version_file_when_no_release_exists(self, tmp_path):
        (tmp_path / "VERSION").write_text("0.9.0\n")
        with patch.object(dashboard, "_cached_latest_release", return_value=None):
            version, commit = dashboard._product_version_and_commit(tmp_path, ("example", "example"), {})

        assert version == "0.9.0"

    def test_falls_back_to_version_file_when_no_owner_repo_configured(self, tmp_path):
        (tmp_path / "VERSION").write_text("0.9.0\n")
        version, commit = dashboard._product_version_and_commit(tmp_path, None, {})
        assert version == "0.9.0"


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

    def test_renders_update_links_for_both_channels(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            page = dashboard._render_page()

        assert f"/update?hc_path={hc_path}&channel=release" in page
        assert f"/update?hc_path={hc_path}&channel=main" in page

    def test_renders_the_horseless_carriage_version_used(self, tmp_path):
        """Distinct from the target repo's own Product Version column -
        this is which version of the HC tooling itself produced this
        project's data (RELEASE.md's "Tracking the HC version in the state
        repo")."""
        hc_path, target_repo = _make_project(tmp_path)
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({"hc_version": "0.2.0"}))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            page = dashboard._render_page()

        assert "0.2.0" in page

    def test_renders_the_hc_commit_hash_alongside_the_hc_version(self, tmp_path):
        """GH issue: hc_version alone (e.g. "1.0.0") doesn't pin down which
        commit at that version actually produced a given sprint's data
        between releases - render it as "1.0.0 (e83912ed)"."""
        hc_path, target_repo = _make_project(tmp_path)
        subprocess.run(["git", "init"], cwd=hc_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=hc_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=hc_path, check=True)
        (hc_path / "VERSION").write_text("1.0.0\n")
        subprocess.run(["git", "add", "VERSION"], cwd=hc_path, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=hc_path, check=True, capture_output=True)
        commit = subprocess.run(
            ["git", "-C", str(hc_path), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({"hc_version": "1.0.0"}))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            page = dashboard._render_page()

        assert f"1.0.0 ({commit})" in page

    def test_renders_a_github_prs_link_when_configured(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        (hc_path / ".env").write_text("GITHUB_REPO_URL='git@github.com:example/example.git'\n")
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            page = dashboard._render_page()

        assert "https://github.com/example/example/pulls" in page
        assert "PRs" in page

    def test_renders_not_configured_when_no_github_repo_url(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            page = dashboard._render_page()

        assert "not configured" in page

    def test_renders_sprint_phase_and_current_ticket_under_the_state_badge(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({
            "sprint_number": 1,
            "sprint_backlog_pr_sprint": 1,
            "sprint_backlog": [{"id": "US-0001", "type": "User Story", "stages_completed": []}],
            "product_backlog": [{"id": "US-0001", "type": "User Story", "title": "Add login", "stages_completed": []}],
        }))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]), \
             patch.object(dashboard.lib_docker, "running_state_repo_mount", return_value=None):
            page = dashboard._render_page()

        assert "Phase: Development" in page
        assert "Ticket: US-0001: Add login" in page

    def test_renders_the_sprint_number_alongside_the_sprint_goal(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        (target_repo / ".hc").mkdir()
        (target_repo / ".hc" / "state.json").write_text(json.dumps({
            "sprint_goal": "Ship the thing", "sprint_number": 3,
        }))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            page = dashboard._render_page()

        assert "Sprint 3:" in page
        assert "Ship the thing" in page

    def test_renders_the_doctor_badge_for_a_project_with_warnings(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}
        items = [doctor.ActionableItem("warning", "GIT_USER_NAME is not set")]

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]), \
             patch.object(dashboard.doctor, "check", return_value=doctor.DoctorResult(items=items)):
            page = dashboard._render_page()

        assert "1 warning(s)" in page
        assert "GIT_USER_NAME is not set" in page

    def test_renders_a_warning_when_the_running_container_has_drifted_from_the_configured_state_repo(self, tmp_path):
        """The actual bug report: a project's sprint goal/latest message
        looked permanently frozen no matter how many times the page
        refreshed - because the running container was still bind-mounted to
        an old STATE_REPO_PATH, not the one .env currently configures, and
        nothing told the user why."""
        hc_path, configured_target_repo = _make_project(tmp_path)
        actually_running_repo = tmp_path / "actually-running"
        (actually_running_repo / ".hc").mkdir(parents=True)
        (actually_running_repo / ".hc" / "state.json").write_text(json.dumps({"sprint_goal": "Real goal"}))
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(configured_target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]), \
             patch.object(dashboard.lib_docker, "running_state_repo_mount",
                           return_value=str(actually_running_repo)):
            page = dashboard._render_page()

        assert "Real goal" in page
        assert "still serving" in page
        assert str(actually_running_repo) in page
        assert str(configured_target_repo) in page

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

        assert "had drifted and was just corrected" in page
        assert str(other_project_repo) in page

    def test_no_drift_warning_when_env_matches_the_registry(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            page = dashboard._render_page()

        assert "had drifted and was just corrected" not in page

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

    def test_renders_green_dot_for_reachable_running_project(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]), \
             patch.object(dashboard.lib_docker, "running_state_repo_mount", return_value=None), \
             patch.object(dashboard, "_url_reachable", return_value=True):
            page = dashboard._render_page()

        assert "#2e7d32" in page
        assert "unreachable" not in page

    def test_renders_red_dot_for_unreachable_running_project(self, tmp_path):
        """The actual bug report: a project shown as 'working' whose
        LiteLLM/web UI links 404/refuse to connect - this must be visible
        right on the row, not only discoverable by clicking through."""
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]), \
             patch.object(dashboard.lib_docker, "running_state_repo_mount", return_value=None), \
             patch.object(dashboard, "_url_reachable", return_value=False):
            page = dashboard._render_page()

        assert "unreachable" in page

    def test_no_reachability_dot_for_resting_project(self, tmp_path):
        hc_path, target_repo = _make_project(tmp_path)
        entry = {"name": "my-project", "hc_path": str(hc_path), "target_repo": str(target_repo)}

        with patch.object(project_registry, "load", return_value=[entry]), \
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=[]):
            page = dashboard._render_page()

        assert "unreachable" not in page
        assert "reachable" not in page

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
             patch.object(dashboard.lib_docker, "compose_running_services", return_value=["agent"]), \
             patch.object(dashboard.lib_docker, "running_state_repo_mount", return_value=None):
            page = dashboard._render_page()

        assert "Approve release PR #42" in page
        assert "http://localhost:8001" in page
        assert "Respond in web UI" in page


class TestMain:
    def test_syncs_the_registry_before_serving(self):
        """GH issue tracker ("old state" report): a dashboard process
        restarted while the registry held a stale/bogus entry (e.g. the
        "." entry setup_llm.py used to create before 933ac89) kept showing
        it until some other render happened to self-heal it - sync_all must
        run eagerly, before the first page is ever served."""
        calls = []

        class _FakeServer:
            def __init__(self, *a, **kw):
                calls.append("server_created")

            def serve_forever(self):
                calls.append("serve_forever")

        with patch.object(dashboard.project_registry, "sync_all", side_effect=lambda: calls.append("sync_all")), \
             patch.object(dashboard, "HTTPServer", _FakeServer), \
             patch.object(dashboard.argparse.ArgumentParser, "parse_args",
                           return_value=dashboard.argparse.Namespace(port=8899, bind_host="127.0.0.1")):
            dashboard.main()

        assert calls == ["sync_all", "server_created", "serve_forever"]


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
