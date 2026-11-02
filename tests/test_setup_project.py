import subprocess

import pytest

import setup_project


def _patch_which(monkeypatch, **available):
    """available maps command name -> bool (True = found)."""
    def fake_which(cmd):
        return f"/usr/bin/{cmd}" if available.get(cmd) else None
    monkeypatch.setattr(setup_project.shutil, "which", fake_which)


@pytest.fixture
def happy_path(monkeypatch):
    """Docker/docker-compose/gh all present and happy, main()'s own
    os.chdir() and .env-from-template copy no-op'd out - isolates the test
    from the real repo's working directory/.env the same way
    tests/test_run.py's TestMainDoctorGatekeeper does for run.py's
    identical os.chdir(Path(__file__)...) pattern."""
    monkeypatch.setattr(setup_project.os, "chdir", lambda _path: None)
    monkeypatch.setattr(setup_project.shutil, "copy", lambda *a, **k: None)
    _patch_which(monkeypatch, docker=True, **{"docker-compose": True, "gh": True})
    monkeypatch.setattr(setup_project.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0))


class TestComposeFileSelection:
    """
    Acceptance Criteria (SPEC-ISSUE-0028): step 4 must reuse whatever compose
    file(s) are actually active (docker-compose.local.yaml [+ the GPU
    override] for a Local/Ollama setup) rather than always defaulting to
    docker-compose.yaml - a bare `docker compose up -d db litellm` here
    previously recreated the litellm container against the WRONG (cloud)
    config immediately after setup_llm.py had just configured and verified
    a Local/Ollama one, since both compose files define a same-named
    `litellm` service.
    """

    def test_cloud_setup_uses_default_compose_file(self, happy_path, monkeypatch, capsys):
        monkeypatch.setattr(setup_project.lib_docker, "compose_file_args", lambda repo_root: [])
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)
        monkeypatch.setattr(setup_project.subprocess, "run", fake_run)

        setup_project.main()

        compose_up_calls = [c for c in calls if c[:2] == ["docker", "compose"] and "up" in c]
        assert compose_up_calls == [["docker", "compose", "-p", "horseless-carriage-dev", "up", "-d", "db", "litellm"]]
        assert "Local/Ollama setup detected" not in capsys.readouterr().out

    def test_local_ollama_setup_uses_local_compose_file(self, happy_path, monkeypatch, capsys):
        monkeypatch.setattr(setup_project.lib_docker, "compose_file_args", lambda repo_root: ["-f", "docker-compose.local.yaml"])
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)
        monkeypatch.setattr(setup_project.subprocess, "run", fake_run)

        setup_project.main()

        compose_up_calls = [c for c in calls if c[:2] == ["docker", "compose"] and "up" in c]
        assert compose_up_calls == [[
            "docker", "compose", "-f", "docker-compose.local.yaml", "-p", "horseless-carriage-dev",
            "up", "-d", "db", "litellm",
        ]]
        assert "Local/Ollama setup detected - using docker-compose.local.yaml" in capsys.readouterr().out

    def test_local_ollama_with_gpu_override_included(self, happy_path, monkeypatch):
        monkeypatch.setattr(
            setup_project.lib_docker, "compose_file_args",
            lambda repo_root: ["-f", "docker-compose.local.yaml", "-f", "docker-compose.gpu.yaml"],
        )
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)
        monkeypatch.setattr(setup_project.subprocess, "run", fake_run)

        setup_project.main()

        compose_up_calls = [c for c in calls if c[:2] == ["docker", "compose"] and "up" in c]
        assert compose_up_calls == [[
            "docker", "compose", "-f", "docker-compose.local.yaml", "-f", "docker-compose.gpu.yaml",
            "-p", "horseless-carriage-dev", "up", "-d", "db", "litellm",
        ]]

    def test_compose_up_failure_exits_with_its_returncode(self, happy_path, monkeypatch):
        monkeypatch.setattr(setup_project.lib_docker, "compose_file_args", lambda repo_root: [])

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["docker", "compose"] and "up" in cmd:
                return subprocess.CompletedProcess(cmd, 1)
            return subprocess.CompletedProcess(cmd, 0)
        monkeypatch.setattr(setup_project.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            setup_project.main()
        assert exc_info.value.code == 1


class TestEnvSecretGeneration:
    """GH issue #482: running this script directly (skipping setup_llm.py's
    interactive flow) used to leave LITELLM_MASTER_KEY/LITELLM_PROXY_API_KEY
    as the literal ".env.example" placeholder text, which ADK then rejected
    with "LiteLLM Virtual Key expected ... expected to start with 'sk-'"."""

    def test_calls_ensure_master_key_and_postgres_password(self, happy_path, monkeypatch):
        calls = []
        monkeypatch.setattr(setup_project.lib_env, "ensure_master_key", lambda p: calls.append(("master_key", p)))
        monkeypatch.setattr(setup_project.lib_env, "ensure_postgres_password", lambda p: calls.append(("postgres_password", p)))

        setup_project.main()

        kinds = [c[0] for c in calls]
        assert "master_key" in kinds
        assert "postgres_password" in kinds


class TestGuardClauses:
    def test_docker_missing_exits(self, monkeypatch):
        monkeypatch.setattr(setup_project.os, "chdir", lambda _path: None)
        _patch_which(monkeypatch)  # nothing available
        with pytest.raises(SystemExit) as exc_info:
            setup_project.main()
        assert exc_info.value.code == 1

    def test_docker_compose_missing_exits(self, monkeypatch):
        monkeypatch.setattr(setup_project.os, "chdir", lambda _path: None)
        _patch_which(monkeypatch, docker=True)

        def raise_called_process_error(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd)
        monkeypatch.setattr(setup_project.subprocess, "run", raise_called_process_error)

        with pytest.raises(SystemExit) as exc_info:
            setup_project.main()
        assert exc_info.value.code == 1
