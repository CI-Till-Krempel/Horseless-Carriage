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
