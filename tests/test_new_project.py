"""
Tests for new_project.py (GH issue #288) - the script that pre-fills
per-installation .env values (STATE_REPO_PATH, COMPOSE_PROJECT_NAME_PREFIX,
host ports) when Horseless Carriage is installed as a submodule of a target
project, so a second/third project's installation doesn't collide with an
already-running one.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import lib_docker
import lib_env
import new_project
import project_registry


class TestConfigureForTargetProject:
    def test_creates_env_from_example_when_missing(self, tmp_path):
        repo_root = tmp_path / "horseless-carriage"
        repo_root.mkdir()
        (repo_root / ".env.example").write_text('SOME_VAR="placeholder"\n')
        target_repo = tmp_path

        with patch.object(lib_docker, "is_port_free", return_value=True):
            new_project._configure_for_target_project(repo_root, target_repo)

        assert (repo_root / ".env").is_file()
        assert 'SOME_VAR="placeholder"' in (repo_root / ".env").read_text()

    def test_defaults_state_repo_path_to_the_target_project(self, tmp_path):
        repo_root = tmp_path / "horseless-carriage"
        repo_root.mkdir()
        (repo_root / ".env.example").write_text("")
        target_repo = tmp_path

        with patch.object(lib_docker, "is_port_free", return_value=True):
            new_project._configure_for_target_project(repo_root, target_repo)

        import lib_env
        assert lib_env.read_env_var(repo_root / ".env", "STATE_REPO_PATH") == str(target_repo)

    def test_derives_compose_project_name_prefix_from_target_repo_name(self, tmp_path):
        repo_root = tmp_path / "My Project" / "horseless-carriage"
        repo_root.mkdir(parents=True)
        (repo_root / ".env.example").write_text("")
        target_repo = tmp_path / "My Project"

        with patch.object(lib_docker, "is_port_free", return_value=True):
            new_project._configure_for_target_project(repo_root, target_repo)

        import lib_env
        assert lib_env.read_env_var(repo_root / ".env", "COMPOSE_PROJECT_NAME_PREFIX") == "horseless-carriage_my-project"

    def test_picks_a_free_port_when_default_is_taken(self, tmp_path):
        repo_root = tmp_path / "horseless-carriage"
        repo_root.mkdir()
        (repo_root / ".env.example").write_text("")
        target_repo = tmp_path

        with patch.object(lib_docker, "is_port_free", side_effect=lambda port: port != 4000), \
             patch.object(lib_docker, "find_free_port", return_value=4001) as mock_find:
            new_project._configure_for_target_project(repo_root, target_repo)

        mock_find.assert_any_call(4001, exclude=set())
        import lib_env
        assert lib_env.read_env_var(repo_root / ".env", "LITELLM_HOST_PORT") == "4001"

    def test_dies_if_no_free_port_found(self, tmp_path):
        repo_root = tmp_path / "horseless-carriage"
        repo_root.mkdir()
        (repo_root / ".env.example").write_text("")
        target_repo = tmp_path

        with patch.object(lib_docker, "is_port_free", return_value=False), \
             patch.object(lib_docker, "find_free_port", return_value=None):
            with pytest.raises(SystemExit):
                new_project._configure_for_target_project(repo_root, target_repo)

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

        with patch.object(lib_docker, "is_port_free", return_value=True):
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

        def fake_configure(repo_root_arg, target_repo_arg):
            # project_registry.sync_from_env reads STATE_REPO_PATH back from
            # .env rather than trusting the caller's own `target_repo`
            # variable - _configure_for_target_project is what really writes
            # it, so the fake must too for this test to exercise the same
            # path real usage does.
            lib_env.update_env_var(repo_root_arg / ".env", "STATE_REPO_PATH", str(target_repo_arg))

        with patch.object(new_project, "_configure_for_target_project", side_effect=fake_configure) as mock_configure, \
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


class TestMainHandsOffSetupToTheAddedProject:
    """GH issue #562 / SPEC-ISSUE-0097: `new_project.py --target-repo X`
    must run the guided setup against X's own horseless-carriage/
    submodule, never against the checkout new_project.py was launched
    from. setup_all.py's steps chdir to their own module's __file__
    directory, so an in-process setup_all.main() (the pre-fix behavior)
    always configured the launching checkout instead."""

    def _fake_submodule(self, tmp_path):
        target_repo = tmp_path / "product"
        repo_root = target_repo / "horseless-carriage"
        repo_root.mkdir(parents=True)
        # A stand-in for the submodule's own setup_all.py: records which
        # directory it ran in and writes "its" config there.
        (repo_root / "setup_all.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "Path('.env').write_text('CONFIGURED=1\\n')\n"
            "(Path(__file__).resolve().parent / 'ran_in.txt').write_text(os.getcwd())\n"
        )
        return target_repo, repo_root

    def test_target_repo_setup_runs_in_the_added_submodule_not_this_checkout(self, tmp_path, monkeypatch):
        target_repo, repo_root = self._fake_submodule(tmp_path)
        launching_checkout = Path(new_project.__file__).resolve().parent
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["new_project.py", "--target-repo", str(target_repo)])

        with patch.object(new_project, "_add_as_submodule", return_value=repo_root), \
             patch.object(new_project, "_install_if_nested"), \
             patch.object(new_project.setup_all, "main") as in_process_setup:
            new_project.main()

        # This checkout's own setup_all.main() (which would chdir back to
        # the launching checkout and rewrite ITS .env) must never run.
        in_process_setup.assert_not_called()
        assert (repo_root / ".env").read_text() == "CONFIGURED=1\n"
        assert Path((repo_root / "ran_in.txt").read_text()).resolve() == repo_root.resolve()
        assert repo_root.resolve() != launching_checkout

    def test_failing_submodule_setup_propagates_its_exit_code(self, tmp_path, monkeypatch):
        target_repo, repo_root = self._fake_submodule(tmp_path)
        (repo_root / "setup_all.py").write_text("import sys\nsys.exit(3)\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["new_project.py", "--target-repo", str(target_repo)])

        with patch.object(new_project, "_add_as_submodule", return_value=repo_root), \
             patch.object(new_project, "_install_if_nested"):
            with pytest.raises(SystemExit) as exc:
                new_project.main()
        assert exc.value.code == 3

    def test_without_target_repo_setup_still_runs_in_process_for_this_checkout(self, monkeypatch):
        launching_checkout = Path(new_project.__file__).resolve().parent
        monkeypatch.setattr("sys.argv", ["new_project.py"])
        monkeypatch.chdir(launching_checkout)

        with patch.object(new_project, "_install_if_nested") as mock_install, \
             patch.object(new_project.setup_all, "main") as in_process_setup, \
             patch.object(new_project.subprocess, "run") as mock_run:
            new_project.main()

        mock_install.assert_called_once_with(launching_checkout)
        in_process_setup.assert_called_once_with()
        mock_run.assert_not_called()
