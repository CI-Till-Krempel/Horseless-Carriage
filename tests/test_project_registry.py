"""Tests for project_registry.py (GH issue #288 / #310)."""
from pathlib import Path
from unittest.mock import patch

import project_registry


class TestLoad:
    def test_returns_empty_list_when_registry_file_missing(self, tmp_path):
        with patch.object(project_registry, "REGISTRY_PATH", tmp_path / "nope.json"):
            assert project_registry.load() == []

    def test_returns_empty_list_when_registry_file_is_corrupted(self, tmp_path):
        registry_path = tmp_path / "projects.json"
        registry_path.write_text("not valid json{{{")
        with patch.object(project_registry, "REGISTRY_PATH", registry_path):
            assert project_registry.load() == []

    def test_loads_existing_projects(self, tmp_path):
        registry_path = tmp_path / "projects.json"
        registry_path.write_text('{"projects": [{"name": "foo", "hc_path": "/a", "target_repo": "/b"}]}')
        with patch.object(project_registry, "REGISTRY_PATH", registry_path):
            assert project_registry.load() == [{"name": "foo", "hc_path": "/a", "target_repo": "/b"}]


class TestRegister:
    def test_adds_a_new_entry(self, tmp_path):
        registry_path = tmp_path / "sub" / "projects.json"
        with patch.object(project_registry, "REGISTRY_PATH", registry_path):
            project_registry.register("my-project", Path("/a/horseless-carriage"), Path("/a"))
            projects = project_registry.load()

        assert len(projects) == 1
        assert projects[0]["name"] == "my-project"
        assert projects[0]["hc_path"] == "/a/horseless-carriage"
        assert projects[0]["target_repo"] == "/a"

    def test_updates_an_existing_entry_matched_by_hc_path_instead_of_duplicating(self, tmp_path):
        registry_path = tmp_path / "projects.json"
        with patch.object(project_registry, "REGISTRY_PATH", registry_path):
            project_registry.register("old-name", Path("/a/horseless-carriage"), Path("/a"))
            project_registry.register("new-name", Path("/a/horseless-carriage"), Path("/a-renamed"))
            projects = project_registry.load()

        assert len(projects) == 1
        assert projects[0]["name"] == "new-name"
        assert projects[0]["target_repo"] == "/a-renamed"

    def test_registering_two_different_projects_keeps_both(self, tmp_path):
        registry_path = tmp_path / "projects.json"
        with patch.object(project_registry, "REGISTRY_PATH", registry_path):
            project_registry.register("project-a", Path("/a/horseless-carriage"), Path("/a"))
            project_registry.register("project-b", Path("/b/horseless-carriage"), Path("/b"))
            projects = project_registry.load()

        assert len(projects) == 2
        assert {p["name"] for p in projects} == {"project-a", "project-b"}


def _nested_hc_path(tmp_path, project_name="target"):
    target_repo = tmp_path / project_name
    (target_repo / ".git").mkdir(parents=True)
    hc_path = target_repo / "horseless-carriage"
    hc_path.mkdir()
    return hc_path, target_repo


class TestSyncFromEnv:
    """GH issue tracker: a real incident - project_registry.json's
    install-time snapshot and a project's own live .env STATE_REPO_PATH had
    diverged (e.g. an .env copied between two projects' submodule checkouts
    to reuse API keys), and nothing ever re-synced the registry afterward -
    dashboard.py silently showed one project's sprint goal under another's
    name. sync_from_env is the one place "what's actually true right now"
    gets (re)written to the registry, called by new_project.py (install),
    setup_llm.py/setup_project.py (every later reconfiguration), and
    dashboard.py (self-heal on render)."""

    def test_registers_using_the_live_env_value(self, tmp_path):
        hc_path, target_repo = _nested_hc_path(tmp_path)
        (hc_path / ".env").write_text(f"STATE_REPO_PATH='{target_repo}'\n")
        registry_path = tmp_path / "projects.json"

        with patch.object(project_registry, "REGISTRY_PATH", registry_path):
            project_registry.sync_from_env(hc_path)
            projects = project_registry.load()

        assert len(projects) == 1
        assert projects[0]["name"] == target_repo.name
        assert projects[0]["hc_path"] == str(hc_path)
        assert projects[0]["target_repo"] == str(target_repo)

    def test_corrects_a_stale_existing_entry(self, tmp_path):
        hc_path, target_repo = _nested_hc_path(tmp_path)
        (hc_path / ".env").write_text(f"STATE_REPO_PATH='{target_repo}'\n")
        registry_path = tmp_path / "projects.json"

        with patch.object(project_registry, "REGISTRY_PATH", registry_path):
            project_registry.register("stale-name", hc_path, tmp_path / "stale-other-project")
            project_registry.sync_from_env(hc_path)
            projects = project_registry.load()

        assert len(projects) == 1
        assert projects[0]["name"] == target_repo.name
        assert projects[0]["target_repo"] == str(target_repo)

    def test_relative_state_repo_path_resolves_against_hc_path(self, tmp_path):
        hc_path, target_repo = _nested_hc_path(tmp_path)
        # hc_path is target_repo/horseless-carriage, so ".." from there is
        # target_repo itself - matches how Docker Compose resolves it too.
        (hc_path / ".env").write_text("STATE_REPO_PATH='..'\n")
        registry_path = tmp_path / "projects.json"

        with patch.object(project_registry, "REGISTRY_PATH", registry_path):
            project_registry.sync_from_env(hc_path)
            projects = project_registry.load()

        assert projects[0]["target_repo"] == str(target_repo.resolve())

    def test_no_op_for_a_standalone_checkout(self, tmp_path):
        """hc_path not nested inside another repo - project_registry only
        tracks new_project.py-installed, multi-project setups."""
        hc_path = tmp_path / "standalone" / "horseless-carriage"
        hc_path.mkdir(parents=True)
        (hc_path / ".env").write_text(f"STATE_REPO_PATH='{tmp_path}'\n")
        registry_path = tmp_path / "projects.json"

        with patch.object(project_registry, "REGISTRY_PATH", registry_path):
            project_registry.sync_from_env(hc_path)

        assert not registry_path.is_file()

    def test_no_op_when_state_repo_path_is_unset(self, tmp_path):
        hc_path, _ = _nested_hc_path(tmp_path)
        (hc_path / ".env").write_text("")
        registry_path = tmp_path / "projects.json"

        with patch.object(project_registry, "REGISTRY_PATH", registry_path):
            project_registry.sync_from_env(hc_path)

        assert not registry_path.is_file()


class TestSyncAll:
    """GH issue tracker ("old state" report): called once by dashboard.py at
    startup. setup_llm.py used to call sync_from_env(Path(".").parent) (fixed
    in 933ac89) - .parent on the literal "." never string-equaled the real
    (absolute) hc_path register() tracks, so every reconfiguration appended/
    updated a bogus {"hc_path": "."} entry instead. That entry's hc_path
    trivially ".is_dir()"s true no matter where dashboard.py is launched
    from (it IS the launch cwd) - project_snapshot's missing-hc_path check
    never caught it, and the dashboard read dashboard.py's own cwd's .env/
    state.json as if it were a registered project's, rendering stale,
    unrelated data under that row. Entries created before the fix are still
    sitting in an existing user's projects.json; this cleans them up."""

    def test_drops_entries_with_a_non_absolute_hc_path(self, tmp_path):
        registry_path = tmp_path / "projects.json"
        registry_path.write_text(
            '{"projects": ['
            '{"name": "bogus", "hc_path": ".", "target_repo": "/whatever"},'
            '{"name": "legit", "hc_path": "' + str(tmp_path) + '", "target_repo": "/b"}'
            ']}'
        )

        with patch.object(project_registry, "REGISTRY_PATH", registry_path):
            project_registry.sync_all()
            projects = project_registry.load()

        assert len(projects) == 1
        assert projects[0]["name"] == "legit"

    def test_leaves_registry_untouched_when_all_entries_are_already_valid(self, tmp_path):
        hc_path, target_repo = _nested_hc_path(tmp_path)
        registry_path = tmp_path / "projects.json"

        with patch.object(project_registry, "REGISTRY_PATH", registry_path):
            project_registry.register("target", hc_path, target_repo)
            before = registry_path.read_text()
            project_registry.sync_all()
            after = registry_path.read_text()

        assert before == after

    def test_resyncs_each_valid_entry_from_its_own_env(self, tmp_path):
        """Catches drift (GH issue tracker's own target_repo-drift incident)
        eagerly at startup instead of only lazily, per-row, on the first
        render that happens to touch a drifted project."""
        hc_path, target_repo = _nested_hc_path(tmp_path)
        other_project_repo = tmp_path / "other-project"
        other_project_repo.mkdir()
        registry_path = tmp_path / "projects.json"

        with patch.object(project_registry, "REGISTRY_PATH", registry_path):
            project_registry.register("stale-name", hc_path, tmp_path / "stale-other-project")
            (hc_path / ".env").write_text(f"STATE_REPO_PATH='{other_project_repo}'\n")
            project_registry.sync_all()
            projects = project_registry.load()

        assert len(projects) == 1
        assert projects[0]["target_repo"] == str(other_project_repo)

    def test_skips_entries_whose_hc_path_no_longer_exists(self, tmp_path):
        """A project whose installation directory has genuinely been
        deleted is a legitimate, intentionally-visible 'missing' row (see
        project_snapshot) - sync_all must not touch or drop it, only entries
        that were never a real registration to begin with."""
        registry_path = tmp_path / "projects.json"
        gone_hc_path = tmp_path / "gone" / "horseless-carriage"

        with patch.object(project_registry, "REGISTRY_PATH", registry_path):
            project_registry.register("gone", gone_hc_path, tmp_path / "gone")
            project_registry.sync_all()
            projects = project_registry.load()

        assert len(projects) == 1
        assert projects[0]["hc_path"] == str(gone_hc_path)
