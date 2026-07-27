import subprocess

import pytest

import check_state_repo


@pytest.fixture
def repo_with_state(tmp_path):
    """Returns (repo_root, state_repo_path) with a valid .env + specs/ dir."""
    state_repo = tmp_path / "state_repo"
    (state_repo / "specs").mkdir(parents=True)
    (tmp_path / ".env").write_text(f'STATE_REPO_PATH="{state_repo}"\n')
    return tmp_path, state_repo


class TestGuardClauses:
    def test_missing_env_file_errors(self, tmp_path, capsys):
        code = check_state_repo.run(tmp_path)
        assert code == 1
        assert ".env file not found" in capsys.readouterr().out

    def test_missing_state_repo_path_errors(self, tmp_path, capsys):
        (tmp_path / ".env").write_text("")
        code = check_state_repo.run(tmp_path)
        assert code == 1
        assert "STATE_REPO_PATH is not set" in capsys.readouterr().out

    def test_state_repo_directory_missing_errors(self, tmp_path, capsys):
        (tmp_path / ".env").write_text('STATE_REPO_PATH="/definitely/missing/xyz"\n')
        code = check_state_repo.run(tmp_path)
        assert code == 1
        assert "does not exist" in capsys.readouterr().out

    def test_specs_directory_missing_errors(self, tmp_path, capsys):
        state_repo = tmp_path / "state_repo"
        state_repo.mkdir()
        (tmp_path / ".env").write_text(f'STATE_REPO_PATH="{state_repo}"\n')
        code = check_state_repo.run(tmp_path)
        assert code == 1
        assert "'specs' directory is missing" in capsys.readouterr().out


class TestStrayTemplates:
    def test_no_stray_templates_reports_ok(self, repo_with_state, capsys):
        repo_root, _state_repo = repo_with_state
        code = check_state_repo.run(repo_root)
        assert code == 0
        assert "[OK] No stray templates found" in capsys.readouterr().out

    def test_stray_template_warns_but_succeeds(self, repo_with_state, capsys):
        repo_root, state_repo = repo_with_state
        (state_repo / "specs" / "TEMPLATE-story.md").write_text("")
        code = check_state_repo.run(repo_root)
        out = capsys.readouterr().out
        assert "Found template files" in out
        assert "TEMPLATE-story.md" in out
        assert code == 0


class TestStateJsonValidation:
    def test_missing_state_json_is_informational_only(self, repo_with_state, capsys):
        repo_root, _state_repo = repo_with_state
        code = check_state_repo.run(repo_root)
        assert code == 0
        assert "state.json not found" in capsys.readouterr().out

    def test_validation_success_via_local_python3(self, repo_with_state, monkeypatch, capsys):
        repo_root, state_repo = repo_with_state
        (state_repo / ".hc").mkdir()
        (state_repo / ".hc" / "state.json").write_text("{}")
        # No docker-compose.yaml in repo_root -> falls back to local python3.
        monkeypatch.setattr(check_state_repo.shutil, "which", lambda cmd: f"/usr/bin/{cmd}" if cmd == "python3" else None)
        monkeypatch.setattr(
            check_state_repo.subprocess, "run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0),
        )
        code = check_state_repo.run(repo_root)
        assert code == 0
        assert "Running validation via local python3" in capsys.readouterr().out

    def test_validation_failure_via_local_python3_fails_the_check(self, repo_with_state, monkeypatch, capsys):
        repo_root, state_repo = repo_with_state
        (state_repo / ".hc").mkdir()
        (state_repo / ".hc" / "state.json").write_text("{}")
        monkeypatch.setattr(check_state_repo.shutil, "which", lambda cmd: f"/usr/bin/{cmd}" if cmd == "python3" else None)
        monkeypatch.setattr(
            check_state_repo.subprocess, "run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1),
        )
        code = check_state_repo.run(repo_root)
        assert code == 1
        assert "state.json validation failed" in capsys.readouterr().out

    def test_validation_prefers_docker_when_available(self, repo_with_state, monkeypatch, capsys):
        repo_root, state_repo = repo_with_state
        (state_repo / ".hc").mkdir()
        (state_repo / ".hc" / "state.json").write_text("{}")
        (repo_root / "docker-compose.yaml").write_text("services: {}\n")

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(check_state_repo.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
        monkeypatch.setattr(check_state_repo.subprocess, "run", fake_run)

        code = check_state_repo.run(repo_root)
        assert code == 0
        assert "Running validation via Docker" in capsys.readouterr().out
        assert calls[0][:3] == ["docker", "compose", "run"]

    def test_no_docker_or_python3_warns_but_does_not_fail(self, repo_with_state, monkeypatch, capsys):
        repo_root, state_repo = repo_with_state
        (state_repo / ".hc").mkdir()
        (state_repo / ".hc" / "state.json").write_text("{}")
        monkeypatch.setattr(check_state_repo.shutil, "which", lambda cmd: None)
        code = check_state_repo.run(repo_root)
        assert code == 0
        assert "Could not find Docker or python3" in capsys.readouterr().out
