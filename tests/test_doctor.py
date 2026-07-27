import subprocess

import pytest

import doctor


def _patch_which(monkeypatch, **available):
    """available maps command name -> bool (True = found)."""
    def fake_which(cmd):
        return f"/usr/bin/{cmd}" if available.get(cmd) else None
    monkeypatch.setattr(doctor.shutil, "which", fake_which)


def _patch_subprocess_ok(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(doctor.subprocess, "run", fake_run)


def _patch_subprocess_fail(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(doctor.subprocess, "run", fake_run)


def _patch_proxy_unreachable(monkeypatch):
    """llm_wait_for_proxy's real implementation retries for wall-clock
    seconds regardless of how fast the connection fails - not worth paying
    for in tests that don't care about proxy reachability at all."""
    monkeypatch.setattr(doctor.lib_llm_test, "llm_wait_for_proxy", lambda *a, **k: False)


@pytest.fixture
def valid_repo(tmp_path, monkeypatch):
    """A tmp_path with a valid .env + litellm.yaml + state repo - everything
    doctor.py needs to pass end-to-end. docker/docker-compose/gh are all
    "present and happy" by default; individual tests override as needed."""
    state_repo = tmp_path / "state_repo"
    (state_repo / "specs").mkdir(parents=True)

    (tmp_path / ".env").write_text(
        'LITELLM_MASTER_KEY="testkey"\n'
        f'STATE_REPO_PATH="{state_repo}"\n'
        'GIT_USER_NAME="Test"\n'
        'GIT_USER_EMAIL="test@example.com"\n'
        'LOG_LEVEL="info"\n'
        'GITHUB_REPO_URL="git@github.com:example/example.git"\n'
        'GITHUB_TOKEN="dummy"\n'
        'GOOGLE_API_KEY="real-key-value"\n'
    )
    (tmp_path / "litellm.yaml").write_text(
        "model_list:\n"
        "  - model_name: scrum-po\n"
        "    litellm_params:\n"
        "      model: gemini/gemini-2.5-pro\n"
        "      api_key: os.environ/GOOGLE_API_KEY\n"
    )

    _patch_which(monkeypatch, docker=True, **{"docker-compose": True, "gh": True})
    _patch_subprocess_ok(monkeypatch)
    return tmp_path


class TestGuardClauses:
    def test_missing_env_file_errors(self, tmp_path, monkeypatch, capsys):
        _patch_which(monkeypatch, docker=True, **{"docker-compose": True})
        code = doctor.run(tmp_path)
        assert code == 1
        assert ".env file not found" in capsys.readouterr().out

    def test_missing_litellm_master_key_errors(self, valid_repo, capsys):
        (valid_repo / ".env").write_text(f'STATE_REPO_PATH="{valid_repo / "state_repo"}"\n')
        code = doctor.run(valid_repo)
        assert code == 1
        assert "LITELLM_MASTER_KEY is not set" in capsys.readouterr().out

    def test_missing_state_repo_path_errors(self, valid_repo, capsys):
        (valid_repo / ".env").write_text('LITELLM_MASTER_KEY="testkey"\n')
        code = doctor.run(valid_repo)
        assert code == 1
        assert "STATE_REPO_PATH is not set" in capsys.readouterr().out

    def test_state_repo_directory_missing_errors(self, valid_repo, capsys):
        (valid_repo / ".env").write_text(
            'LITELLM_MASTER_KEY="testkey"\nSTATE_REPO_PATH="/definitely/missing/xyz"\n'
        )
        code = doctor.run(valid_repo)
        assert code == 1
        assert "does not exist" in capsys.readouterr().out

    def test_docker_missing_errors(self, valid_repo, monkeypatch, capsys):
        _patch_which(monkeypatch)  # nothing available
        code = doctor.run(valid_repo)
        assert code == 1
        assert "'docker' command not found" in capsys.readouterr().out

    def test_docker_compose_missing_errors(self, valid_repo, monkeypatch, capsys):
        _patch_which(monkeypatch, docker=True)
        _patch_subprocess_fail(monkeypatch)
        code = doctor.run(valid_repo)
        assert code == 1
        assert "'docker-compose' or 'docker compose' command not found" in capsys.readouterr().out


class TestWarningsDoNotBlock:
    def test_missing_git_identity_warns_but_succeeds(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        text = env.read_text().replace('GIT_USER_NAME="Test"\n', "").replace('GIT_USER_EMAIL="test@example.com"\n', "")
        env.write_text(text)
        code = doctor.run(valid_repo)
        out = capsys.readouterr().out
        assert "GIT_USER_NAME is not set" in out
        assert "GIT_USER_EMAIL is not set" in out
        assert code == 0

    def test_github_token_reports_personal_access_token(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        code = doctor.run(valid_repo)
        assert "Using Personal Access Token" in capsys.readouterr().out
        assert code == 0

    def test_github_app_trio_reports_github_app(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        text = env.read_text().replace('GITHUB_TOKEN="dummy"\n', "")
        text += 'GITHUB_APP_ID="1"\nGITHUB_APP_PRIVATE_KEY="key"\nGITHUB_APP_INSTALLATION_ID="2"\n'
        env.write_text(text)
        code = doctor.run(valid_repo)
        assert "Using GitHub App" in capsys.readouterr().out
        assert code == 0

    def test_no_github_auth_warns(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        env.write_text(env.read_text().replace('GITHUB_TOKEN="dummy"\n', ""))
        code = doctor.run(valid_repo)
        assert "No GitHub authentication method fully configured" in capsys.readouterr().out
        assert code == 0

    def test_sessions_directory_created_if_missing(self, valid_repo, monkeypatch):
        _patch_proxy_unreachable(monkeypatch)
        assert not (valid_repo / "sessions").exists()
        doctor.run(valid_repo)
        assert (valid_repo / "sessions").is_dir()

    def test_gh_missing_warns(self, valid_repo, monkeypatch, capsys):
        _patch_which(monkeypatch, docker=True, **{"docker-compose": True})
        _patch_proxy_unreachable(monkeypatch)
        code = doctor.run(valid_repo)
        assert "'gh' command not found" in capsys.readouterr().out
        assert code == 0

    def test_gh_not_authenticated_warns(self, valid_repo, monkeypatch, capsys):
        _patch_which(monkeypatch, docker=True, **{"docker-compose": True, "gh": True})
        _patch_subprocess_fail(monkeypatch)
        _patch_proxy_unreachable(monkeypatch)
        code = doctor.run(valid_repo)
        assert "gh CLI is not authenticated" in capsys.readouterr().out
        assert code == 0


class TestLlmConfigurationSection:
    def test_active_provider_detected_and_placeholder_key_warns(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        env = valid_repo / ".env"
        env.write_text(env.read_text().replace('GOOGLE_API_KEY="real-key-value"\n', 'GOOGLE_API_KEY="<your_google_key>"\n'))
        doctor.run(valid_repo)
        out = capsys.readouterr().out
        assert "Active provider (litellm.yaml): gemini" in out
        assert "GOOGLE_API_KEY is not set (or still a placeholder)" in out

    def test_proxy_unreachable_cloud_hint(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        doctor.run(valid_repo)
        out = capsys.readouterr().out
        assert "not reachable" in out
        assert "docker compose up -d db litellm" in out

    def test_proxy_unreachable_local_hint(self, valid_repo, monkeypatch, capsys):
        _patch_proxy_unreachable(monkeypatch)
        (valid_repo / "litellm.yaml").write_text(
            "model_list:\n"
            "  - model_name: scrum-po\n"
            "    litellm_params:\n"
            "      model: ollama/llama3.1:8b\n"
            "      api_base: http://ollama:11434\n"
        )
        doctor.run(valid_repo)
        out = capsys.readouterr().out
        assert "docker-compose.local.yaml up -d db litellm ollama" in out

    def test_proxy_reachable_and_test_succeeds(self, valid_repo, mock_proxy, capsys):
        base_url, behavior = mock_proxy
        env = valid_repo / ".env"
        env.write_text(env.read_text().replace('LITELLM_MASTER_KEY="testkey"\n', f'LITELLM_MASTER_KEY="{behavior["valid_key"]}"\n'))
        code = doctor.run(valid_repo, proxy_base_url=base_url)
        out = capsys.readouterr().out
        assert f"LiteLLM proxy: reachable at {base_url}" in out
        assert "LLM connectivity: OK" in out
        assert code == 0

    def test_proxy_reachable_but_auth_fails(self, valid_repo, mock_proxy, capsys):
        base_url, _behavior = mock_proxy
        # .env's LITELLM_MASTER_KEY ("testkey") does not match the mock's
        # default valid_key ("good-key") - simulates a real key mismatch.
        code = doctor.run(valid_repo, proxy_base_url=base_url)
        out = capsys.readouterr().out
        assert "LLM connectivity test failed" in out
        assert "401" in out
        assert code == 0  # a failed live test is a warning, not a hard failure
