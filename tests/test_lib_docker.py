import json
import subprocess

import lib_docker


def _completed(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


class TestComposeRunningServices:
    def test_returns_service_names_from_json_lines(self, monkeypatch):
        stdout = "\n".join([
            json.dumps({"Service": "db", "Name": "hc-db-1"}),
            json.dumps({"Service": "litellm", "Name": "hc-litellm-1"}),
        ])
        monkeypatch.setattr(lib_docker.subprocess, "run", lambda *a, **k: _completed(a[0], 0, stdout))
        assert lib_docker.compose_running_services([]) == ["db", "litellm"]

    def test_returns_service_names_from_json_array(self, monkeypatch):
        stdout = json.dumps([{"Service": "db"}, {"Service": "litellm"}])
        monkeypatch.setattr(lib_docker.subprocess, "run", lambda *a, **k: _completed(a[0], 0, stdout))
        assert lib_docker.compose_running_services([]) == ["db", "litellm"]

    def test_nothing_running_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(lib_docker.subprocess, "run", lambda *a, **k: _completed(a[0], 0, ""))
        assert lib_docker.compose_running_services([]) == []

    def test_command_failure_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(lib_docker.subprocess, "run", lambda *a, **k: _completed(a[0], 1, "", "some docker error"))
        assert lib_docker.compose_running_services([]) == []

    def test_docker_not_installed_returns_empty_list_instead_of_raising(self, monkeypatch):
        def raise_missing(*a, **k):
            raise FileNotFoundError("no such file: docker")
        monkeypatch.setattr(lib_docker.subprocess, "run", raise_missing)
        assert lib_docker.compose_running_services([]) == []

    def test_unparsable_output_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(lib_docker.subprocess, "run", lambda *a, **k: _completed(a[0], 0, "not json"))
        assert lib_docker.compose_running_services([]) == []


class TestOllamaGpuStatus:
    def test_cuda_log_line_returns_cuda(self, monkeypatch):
        stdout = 'ollama-1  | time=2026-07-29 level=INFO source=gpu.go msg="inference compute" id=0 library=cuda variant=v12 compute=8.9 driver=12.4 name="NVIDIA GeForce RTX 4090" total="24.0 GiB" available="23.1 GiB"\n'
        monkeypatch.setattr(lib_docker.subprocess, "run", lambda *a, **k: _completed(a[0], 0, stdout))
        assert lib_docker.ollama_gpu_status([]) == "cuda"

    def test_cpu_log_line_returns_cpu(self, monkeypatch):
        stdout = 'ollama-1  | time=2026-07-29 level=INFO source=gpu.go msg="inference compute" id=0 library=cpu variant=avx2 compute="" driver=0.0 name="" total="0 B" available="0 B"\n'
        monkeypatch.setattr(lib_docker.subprocess, "run", lambda *a, **k: _completed(a[0], 0, stdout))
        assert lib_docker.ollama_gpu_status([]) == "cpu"

    def test_last_matching_line_wins(self, monkeypatch):
        stdout = (
            'ollama-1  | msg="inference compute" id=0 library=cpu\n'
            'ollama-1  | msg="inference compute" id=0 library=cuda\n'
        )
        monkeypatch.setattr(lib_docker.subprocess, "run", lambda *a, **k: _completed(a[0], 0, stdout))
        assert lib_docker.ollama_gpu_status([]) == "cuda"

    def test_no_matching_line_returns_none(self, monkeypatch):
        monkeypatch.setattr(lib_docker.subprocess, "run", lambda *a, **k: _completed(a[0], 0, "Waiting for Ollama to start...\n"))
        assert lib_docker.ollama_gpu_status([]) is None

    def test_command_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(lib_docker.subprocess, "run", lambda *a, **k: _completed(a[0], 1, "", "some docker error"))
        assert lib_docker.ollama_gpu_status([]) is None

    def test_docker_not_installed_returns_none_instead_of_raising(self, monkeypatch):
        def raise_missing(*a, **k):
            raise FileNotFoundError("no such file: docker")
        monkeypatch.setattr(lib_docker.subprocess, "run", raise_missing)
        assert lib_docker.ollama_gpu_status([]) is None


class TestMaybeStopExistingStack:
    def test_nothing_running_does_not_prompt(self, monkeypatch, capsys):
        monkeypatch.setattr(lib_docker, "compose_running_services", lambda compose_args: [])

        def fail_if_called(*a, **k):
            raise AssertionError("input() should not be called when nothing is running")
        monkeypatch.setattr("builtins.input", fail_if_called)

        lib_docker.maybe_stop_existing_stack([])
        assert "already running" not in capsys.readouterr().out

    def test_declining_leaves_stack_running(self, monkeypatch, capsys):
        monkeypatch.setattr(lib_docker, "compose_running_services", lambda compose_args: ["db", "litellm"])
        monkeypatch.setattr("builtins.input", lambda _: "n")

        def fail_if_called(*a, **k):
            raise AssertionError("docker compose down should not run when the user declines")
        monkeypatch.setattr(lib_docker.subprocess, "run", fail_if_called)

        lib_docker.maybe_stop_existing_stack([])
        out = capsys.readouterr().out
        assert "already running" in out
        assert "db" in out and "litellm" in out

    def test_empty_input_defaults_to_declining(self, monkeypatch):
        monkeypatch.setattr(lib_docker, "compose_running_services", lambda compose_args: ["db"])
        monkeypatch.setattr("builtins.input", lambda _: "")

        def fail_if_called(*a, **k):
            raise AssertionError("docker compose down should not run on empty/default input")
        monkeypatch.setattr(lib_docker.subprocess, "run", fail_if_called)

        lib_docker.maybe_stop_existing_stack([])

    def test_accepting_runs_compose_down(self, monkeypatch, capsys):
        monkeypatch.setattr(lib_docker, "compose_running_services", lambda compose_args: ["db"])
        monkeypatch.setattr("builtins.input", lambda _: "y")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _completed(cmd, 0)
        monkeypatch.setattr(lib_docker.subprocess, "run", fake_run)

        lib_docker.maybe_stop_existing_stack(["-f", "docker-compose.local.yaml"])

        assert calls == [["docker", "compose", "-f", "docker-compose.local.yaml", "down"]]
        assert "Stopping the existing stack" in capsys.readouterr().out

    def test_compose_down_failure_warns_but_does_not_raise(self, monkeypatch, capsys):
        monkeypatch.setattr(lib_docker, "compose_running_services", lambda compose_args: ["db"])
        monkeypatch.setattr("builtins.input", lambda _: "y")
        monkeypatch.setattr(lib_docker.subprocess, "run", lambda cmd, **k: _completed(cmd, 1))

        lib_docker.maybe_stop_existing_stack([])

        assert "did not complete cleanly" in capsys.readouterr().out
