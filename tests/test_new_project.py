"""
Tests for new_project.py (GH issue #288) - the script that pre-fills
per-installation .env values (STATE_REPO_PATH, COMPOSE_PROJECT_NAME_PREFIX,
host ports) when Horseless Carriage is installed as a submodule of a target
project, so a second/third project's installation doesn't collide with an
already-running one.
"""
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import new_project
import project_registry


class TestSanitizeComposeProjectPrefix:
    def test_lowercases_and_replaces_spaces(self):
        assert new_project._sanitize_compose_project_prefix("My Cool Project") == "my-cool-project"

    def test_already_valid_name_is_unchanged(self):
        assert new_project._sanitize_compose_project_prefix("my-cool-project") == "my-cool-project"

    def test_collapses_repeated_separators(self):
        assert new_project._sanitize_compose_project_prefix("Project_2.0!!") == "project-2-0"

    def test_leading_digit_is_fine(self):
        # Compose project names must start with [a-z0-9] - a leading digit
        # is valid, unlike a leading dash would be.
        assert new_project._sanitize_compose_project_prefix("123abc") == "123abc"

    def test_falls_back_to_a_safe_default_when_nothing_survives(self):
        assert new_project._sanitize_compose_project_prefix("___") == "hc-project"
        assert new_project._sanitize_compose_project_prefix("") == "hc-project"


class TestFreePortDetection:
    def test_is_port_free_true_for_an_unbound_port(self):
        # Bind briefly to find a genuinely free ephemeral port, then release
        # it and confirm _is_port_free agrees.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        assert new_project._is_port_free(port) is True

    def test_is_port_free_false_for_a_bound_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
            assert new_project._is_port_free(port) is False

    def test_find_free_port_returns_preferred_when_free(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        assert new_project._find_free_port(free_port) == free_port

    def test_find_free_port_skips_a_bound_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            bound_port = s.getsockname()[1]
            result = new_project._find_free_port(bound_port)
            assert result != bound_port

    def test_find_free_port_dies_if_nothing_free_in_range(self):
        with patch.object(new_project, "_is_port_free", return_value=False):
            with pytest.raises(SystemExit):
                new_project._find_free_port(4000, max_attempts=3)


class TestConfigureForTargetProject:
    def test_creates_env_from_example_when_missing(self, tmp_path):
        repo_root = tmp_path / "horseless-carriage"
        repo_root.mkdir()
        (repo_root / ".env.example").write_text('SOME_VAR="placeholder"\n')
        target_repo = tmp_path

        with patch.object(new_project, "_is_port_free", return_value=True):
            new_project._configure_for_target_project(repo_root, target_repo)

        assert (repo_root / ".env").is_file()
        assert 'SOME_VAR="placeholder"' in (repo_root / ".env").read_text()

    def test_defaults_state_repo_path_to_the_target_project(self, tmp_path):
        repo_root = tmp_path / "horseless-carriage"
        repo_root.mkdir()
        (repo_root / ".env.example").write_text("")
        target_repo = tmp_path

        with patch.object(new_project, "_is_port_free", return_value=True):
            new_project._configure_for_target_project(repo_root, target_repo)

        import lib_env
        assert lib_env.read_env_var(repo_root / ".env", "STATE_REPO_PATH") == str(target_repo)

    def test_derives_compose_project_name_prefix_from_target_repo_name(self, tmp_path):
        repo_root = tmp_path / "My Project" / "horseless-carriage"
        repo_root.mkdir(parents=True)
        (repo_root / ".env.example").write_text("")
        target_repo = tmp_path / "My Project"

        with patch.object(new_project, "_is_port_free", return_value=True):
            new_project._configure_for_target_project(repo_root, target_repo)

        import lib_env
        assert lib_env.read_env_var(repo_root / ".env", "COMPOSE_PROJECT_NAME_PREFIX") == "my-project"

    def test_picks_a_free_port_when_default_is_taken(self, tmp_path):
        repo_root = tmp_path / "horseless-carriage"
        repo_root.mkdir()
        (repo_root / ".env.example").write_text("")
        target_repo = tmp_path

        with patch.object(new_project, "_is_port_free", side_effect=lambda port: port != 4000), \
             patch.object(new_project, "_find_free_port", return_value=4001) as mock_find:
            new_project._configure_for_target_project(repo_root, target_repo)

        mock_find.assert_any_call(4001)
        import lib_env
        assert lib_env.read_env_var(repo_root / ".env", "LITELLM_HOST_PORT") == "4001"

    def test_never_overwrites_an_already_configured_value(self, tmp_path):
        """A re-run (e.g. after `git pull`-ing a newer Horseless Carriage
        into an existing submodule) must not clobber a choice already made
        for this project."""
        repo_root = tmp_path / "horseless-carriage"
        repo_root.mkdir()
        (repo_root / ".env").write_text(
            "STATE_REPO_PATH='/somewhere/custom'\n"
            "COMPOSE_PROJECT_NAME_PREFIX='custom-prefix'\n"
            "LITELLM_HOST_PORT='9999'\n"
        )
        target_repo = tmp_path

        with patch.object(new_project, "_is_port_free", return_value=True):
            new_project._configure_for_target_project(repo_root, target_repo)

        import lib_env
        env_path = repo_root / ".env"
        assert lib_env.read_env_var(env_path, "STATE_REPO_PATH") == "/somewhere/custom"
        assert lib_env.read_env_var(env_path, "COMPOSE_PROJECT_NAME_PREFIX") == "custom-prefix"
        assert lib_env.read_env_var(env_path, "LITELLM_HOST_PORT") == "9999"


class TestAddAsSubmodule:
    def test_dies_if_this_checkout_has_no_git_remote(self, tmp_path):
        this_checkout = tmp_path / "hc-checkout"
        this_checkout.mkdir()
        target_repo = tmp_path / "target"

        with patch.object(new_project, "_git_remote_url", return_value=""):
            with pytest.raises(SystemExit):
                new_project._add_as_submodule(this_checkout, target_repo)

    def test_skips_submodule_add_when_submodule_dir_already_exists(self, tmp_path):
        this_checkout = tmp_path / "hc-checkout"
        this_checkout.mkdir()
        target_repo = tmp_path / "target"
        (target_repo / "horseless-carriage").mkdir(parents=True)

        with patch.object(new_project, "_git_remote_url", return_value="git@github.com:org/repo.git"), \
             patch("subprocess.run") as mock_run:
            result = new_project._add_as_submodule(this_checkout, target_repo)

        mock_run.assert_not_called()
        assert result == target_repo / "horseless-carriage"

    def test_runs_git_submodule_add_with_the_detected_remote_url(self, tmp_path):
        this_checkout = tmp_path / "hc-checkout"
        this_checkout.mkdir()
        target_repo = tmp_path / "target"
        target_repo.mkdir()
        (target_repo / ".git").mkdir()

        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch.object(new_project, "_git_remote_url", return_value="git@github.com:org/repo.git"), \
             patch("subprocess.run", return_value=mock_result) as mock_run:
            result = new_project._add_as_submodule(this_checkout, target_repo)

        mock_run.assert_called_once_with(
            ["git", "-C", str(target_repo), "submodule", "add", "git@github.com:org/repo.git", "horseless-carriage"],
            capture_output=True, text=True,
        )
        assert result == target_repo / "horseless-carriage"

    def test_dies_if_git_submodule_add_fails(self, tmp_path):
        this_checkout = tmp_path / "hc-checkout"
        this_checkout.mkdir()
        target_repo = tmp_path / "target"
        target_repo.mkdir()
        (target_repo / ".git").mkdir()

        mock_result = MagicMock(returncode=1, stdout="", stderr="fatal: error")
        with patch.object(new_project, "_git_remote_url", return_value="git@github.com:org/repo.git"), \
             patch("subprocess.run", return_value=mock_result):
            with pytest.raises(SystemExit):
                new_project._add_as_submodule(this_checkout, target_repo)


class TestInstallIfNested:
    def test_registers_the_project_when_nested_inside_another_repo(self, tmp_path):
        target_repo = tmp_path / "target"
        target_repo.mkdir()
        (target_repo / ".git").mkdir()
        repo_root = target_repo / "horseless-carriage"
        repo_root.mkdir()

        registry_path = tmp_path / "registry.json"
        with patch.object(new_project, "_configure_for_target_project") as mock_configure, \
             patch.object(project_registry, "REGISTRY_PATH", registry_path):
            new_project._install_if_nested(repo_root)
            projects = project_registry.load()

        mock_configure.assert_called_once_with(repo_root, target_repo)
        assert len(projects) == 1
        assert projects[0]["name"] == "target"
        assert projects[0]["hc_path"] == str(repo_root)
        assert projects[0]["target_repo"] == str(target_repo)

    def test_does_nothing_when_standalone_not_nested_in_a_repo(self, tmp_path):
        repo_root = tmp_path / "standalone-checkout"
        repo_root.mkdir()

        registry_path = tmp_path / "registry.json"
        with patch.object(new_project, "_configure_for_target_project") as mock_configure, \
             patch.object(project_registry, "REGISTRY_PATH", registry_path):
            new_project._install_if_nested(repo_root)

        mock_configure.assert_not_called()
        assert not registry_path.is_file()
