import json
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


def _fail_validation(monkeypatch):
    """Makes check_state_repo.run()'s validate_state.py step always report
    GENUINE corruption (exit code 3 - see check_state_repo.
    VALIDATE_STATE_CORRUPTION_EXIT_CODE / GH issue #109), via local python3
    (no docker-compose.yaml in the fixture repo_root), regardless of
    state.json's actual real content - the tests below only care about what
    happens *after* validation fails. Only intercepts the validate_state.py
    invocation itself; every other subprocess.run call (the git commands
    _offer_state_repair's "reset from git history" option makes) passes
    through to the real subprocess.run, since those need to actually work
    against the fixture's real git repo."""
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if any("validate_state.py" in str(part) for part in cmd):
            return subprocess.CompletedProcess(cmd, check_state_repo.VALIDATE_STATE_CORRUPTION_EXIT_CODE)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(check_state_repo.shutil, "which", lambda cmd: f"/usr/bin/{cmd}" if cmd == "python3" else None)
    monkeypatch.setattr(check_state_repo.subprocess, "run", fake_run)


def _init_git_repo(repo_path):
    subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)


def _commit_state_json(state_repo, content, message):
    (state_repo / ".hc").mkdir(parents=True, exist_ok=True)
    (state_repo / ".hc" / "state.json").write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", ".hc/state.json"], cwd=state_repo, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=state_repo, check=True)


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
            lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, check_state_repo.VALIDATE_STATE_CORRUPTION_EXIT_CODE),
        )
        code = check_state_repo.run(repo_root, interactive=False)
        assert code == 1
        assert "state.json validation failed" in capsys.readouterr().out

    def test_validation_script_itself_failing_to_run_is_not_treated_as_corruption(self, repo_with_state, monkeypatch, capsys):
        """
        Acceptance Criteria (GH issue #109): a non-zero exit code from
        validate_state.py that ISN'T the dedicated corruption exit code
        (e.g. 1, from an ImportError, the Docker daemon being down, or any
        other environment problem) means the validation script itself
        couldn't run - it must NOT be reported/treated as "state.json is
        corrupted", and must never trigger the repair/reset/delete menu.
        """
        repo_root, state_repo = repo_with_state
        (state_repo / ".hc").mkdir()
        (state_repo / ".hc" / "state.json").write_text("{}")
        monkeypatch.setattr(check_state_repo.shutil, "which", lambda cmd: f"/usr/bin/{cmd}" if cmd == "python3" else None)
        monkeypatch.setattr(
            check_state_repo.subprocess, "run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1),
        )
        code = check_state_repo.run(repo_root, interactive=True, prompt=lambda _: (_ for _ in ()).throw(AssertionError("must not prompt")))
        out = capsys.readouterr().out
        assert "state.json validation failed" not in out
        assert "might be corrupted" not in out
        assert "Could not validate state.json" in out
        assert code == 0

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


class TestInteractiveStateRepair:
    """
    Acceptance Criteria (GH issue #85 - "Offer options to repair or delete
    corrupted state"): a corrupted state.json, detected interactively,
    offers a menu of remediation options rather than just failing with a
    dead-end error message.
    """

    def test_non_interactive_still_just_fails(self, repo_with_state, monkeypatch, capsys):
        """The pre-existing non-interactive behavior (e.g. doctor.py/CI)
        must be unchanged - no prompt, no blocking on input()."""
        repo_root, state_repo = repo_with_state
        (state_repo / ".hc").mkdir()
        (state_repo / ".hc" / "state.json").write_text("{not valid json")
        _fail_validation(monkeypatch)

        code = check_state_repo.run(repo_root, interactive=False)

        assert code == 1
        assert "What would you like to do?" not in capsys.readouterr().out

    def test_reset_from_git_history_option(self, repo_with_state, monkeypatch, capsys):
        repo_root, state_repo = repo_with_state
        _init_git_repo(state_repo)
        _commit_state_json(state_repo, json.dumps({"sprint_goal": "Good checkpoint"}), "good checkpoint")
        (state_repo / ".hc" / "state.json").write_text("{not valid json - corrupted after the good commit")
        _fail_validation(monkeypatch)

        code = check_state_repo.run(repo_root, interactive=True, prompt=lambda _msg: "1")

        assert code == 0
        restored = json.loads((state_repo / ".hc" / "state.json").read_text())
        assert restored["sprint_goal"] == "Good checkpoint"
        assert "Restored state.json from the last known-good checkpoint" in capsys.readouterr().out

    def test_reset_from_git_history_walks_past_a_corrupted_head_commit(self, repo_with_state, monkeypatch):
        """If the *latest* commit's own snapshot is itself corrupted, an
        earlier good one must still be found - not just HEAD."""
        repo_root, state_repo = repo_with_state
        _init_git_repo(state_repo)
        _commit_state_json(state_repo, json.dumps({"sprint_goal": "Good checkpoint"}), "good checkpoint")
        _commit_state_json(state_repo, "{not valid json - this commit is ALSO corrupted", "corrupted checkpoint")
        (state_repo / ".hc" / "state.json").write_text("{not valid json - current working tree")
        _fail_validation(monkeypatch)

        code = check_state_repo.run(repo_root, interactive=True, prompt=lambda _msg: "1")

        assert code == 0
        restored = json.loads((state_repo / ".hc" / "state.json").read_text())
        assert restored["sprint_goal"] == "Good checkpoint"

    def test_reset_from_git_history_reports_error_with_no_checkpoint(self, repo_with_state, monkeypatch, capsys):
        repo_root, state_repo = repo_with_state
        _init_git_repo(state_repo)
        (state_repo / ".hc").mkdir()
        (state_repo / ".hc" / "state.json").write_text("{not valid json")
        _fail_validation(monkeypatch)

        code = check_state_repo.run(repo_root, interactive=True, prompt=lambda _msg: "1")

        assert code == 1
        assert "No usable checkpoint found" in capsys.readouterr().out

    def test_delete_option(self, repo_with_state, monkeypatch, capsys):
        repo_root, state_repo = repo_with_state
        (state_repo / ".hc").mkdir()
        (state_repo / ".hc" / "state.json").write_text("{not valid json")
        _fail_validation(monkeypatch)

        code = check_state_repo.run(repo_root, interactive=True, prompt=lambda _msg: "2")

        assert code == 0
        assert not (state_repo / ".hc" / "state.json").exists()
        assert "Deleted the corrupted state.json" in capsys.readouterr().out

    def test_leave_as_is_option(self, repo_with_state, monkeypatch, capsys):
        repo_root, state_repo = repo_with_state
        (state_repo / ".hc").mkdir()
        (state_repo / ".hc" / "state.json").write_text("{not valid json")
        _fail_validation(monkeypatch)

        code = check_state_repo.run(repo_root, interactive=True, prompt=lambda _msg: "3")

        assert code == 1
        assert (state_repo / ".hc" / "state.json").exists()

    def test_empty_input_defaults_to_leave_as_is(self, repo_with_state, monkeypatch, capsys):
        repo_root, state_repo = repo_with_state
        (state_repo / ".hc").mkdir()
        (state_repo / ".hc" / "state.json").write_text("{not valid json")
        _fail_validation(monkeypatch)

        code = check_state_repo.run(repo_root, interactive=True, prompt=lambda _msg: "")

        assert code == 1
        assert (state_repo / ".hc" / "state.json").exists()
