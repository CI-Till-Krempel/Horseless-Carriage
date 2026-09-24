import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import run


class TestParseArgs:
    def test_defaults(self):
        assert run.parse_args([]) == ("web", False, False, [])

    def test_cli_mode_with_query_args(self):
        assert run.parse_args(["cli", "hello", "world"]) == ("cli", False, False, ["hello", "world"])

    def test_daemon_flag(self):
        assert run.parse_args(["daemon"]) == ("web", True, False, [])

    def test_cli_and_daemon_combined(self):
        assert run.parse_args(["cli", "daemon", "query"]) == ("cli", True, False, ["query"])

    def test_last_mode_keyword_wins(self):
        # Mirrors the bash version's plain for-loop scan: later keywords override earlier ones.
        assert run.parse_args(["cli", "web"]) == ("web", False, False, [])

    def test_dev_flag(self):
        assert run.parse_args(["dev"]) == ("web", False, True, [])

    def test_dev_daemon_and_cli_combined(self):
        assert run.parse_args(["cli", "daemon", "dev", "query"]) == ("cli", True, True, ["query"])


class _OkHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()


@pytest.fixture
def ok_server():
    server = HTTPServer(("127.0.0.1", 0), _OkHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


class TestComposeFileArgs:
    """
    Acceptance Criteria (GH issue #36): run.py must launch the agent stack
    against docker-compose.local.yaml for a Local/Ollama setup - it defines
    its own litellm/ollama/agent services pointed at
    config/model-templates/litellm.local-ollama.yaml, unlike the default
    docker-compose.yaml which always mounts the root litellm.yaml.
    """

    def test_cloud_setup_uses_default_compose_file(self, tmp_path):
        (tmp_path / "litellm.yaml").write_text(
            "model_list:\n  - model_name: scrum-po\n    litellm_params:\n      model: gemini/gemini-2.5-pro\n"
        )
        assert run.compose_file_args(tmp_path) == []

    def test_local_setup_uses_local_compose_file(self, tmp_path):
        local_yaml = tmp_path / "config" / "model-templates" / "litellm.local-ollama.yaml"
        local_yaml.parent.mkdir(parents=True)
        local_yaml.write_text(
            "model_list:\n  - model_name: scrum-po\n    litellm_params:\n      model: ollama/llama3.1:8b\n"
        )
        assert run.compose_file_args(tmp_path) == ["-f", "docker-compose.local.yaml"]

    def test_no_config_at_all_defaults_to_default_compose_file(self, tmp_path):
        assert run.compose_file_args(tmp_path) == []

    def test_local_setup_with_gpu_enabled_adds_gpu_compose_file(self, tmp_path):
        """OLLAMA_GPU_ENABLED=true (set by setup_llm.py's GPU prompt) must
        merge in docker-compose.gpu.yaml automatically - the user shouldn't
        need to remember to pass -f docker-compose.gpu.yaml by hand every
        time they start the agent."""
        local_yaml = tmp_path / "config" / "model-templates" / "litellm.local-ollama.yaml"
        local_yaml.parent.mkdir(parents=True)
        local_yaml.write_text(
            "model_list:\n  - model_name: scrum-po\n    litellm_params:\n      model: ollama/llama3.1:8b\n"
        )
        (tmp_path / ".env").write_text("OLLAMA_GPU_ENABLED='true'\n")
        assert run.compose_file_args(tmp_path) == [
            "-f", "docker-compose.local.yaml", "-f", "docker-compose.gpu.yaml",
        ]

    def test_local_setup_with_gpu_disabled_omits_gpu_compose_file(self, tmp_path):
        local_yaml = tmp_path / "config" / "model-templates" / "litellm.local-ollama.yaml"
        local_yaml.parent.mkdir(parents=True)
        local_yaml.write_text(
            "model_list:\n  - model_name: scrum-po\n    litellm_params:\n      model: ollama/llama3.1:8b\n"
        )
        (tmp_path / ".env").write_text("OLLAMA_GPU_ENABLED='false'\n")
        assert run.compose_file_args(tmp_path) == ["-f", "docker-compose.local.yaml"]

    def test_cloud_setup_ignores_gpu_env_var(self, tmp_path):
        """OLLAMA_GPU_ENABLED is meaningless for a cloud provider - it must
        never sneak docker-compose.gpu.yaml into a cloud setup's args."""
        (tmp_path / "litellm.yaml").write_text(
            "model_list:\n  - model_name: scrum-po\n    litellm_params:\n      model: gemini/gemini-2.5-pro\n"
        )
        (tmp_path / ".env").write_text("OLLAMA_GPU_ENABLED='true'\n")
        assert run.compose_file_args(tmp_path) == []

    def test_local_setup_with_host_mode_uses_hostollama_compose_file(self, tmp_path):
        """OLLAMA_HOST_MODE=true (GH issue #93: Ollama running natively on
        the host, e.g. macOS with no GPU passthrough into Docker) must use
        docker-compose.local-hostollama.yaml INSTEAD OF docker-compose.local
        .yaml, not merged alongside it - Compose merges (rather than
        replaces) `depends_on` across `-f` files, so an overlay alone can't
        remove litellm's dependency on the dockerized `ollama` service."""
        local_yaml = tmp_path / "config" / "model-templates" / "litellm.local-ollama.yaml"
        local_yaml.parent.mkdir(parents=True)
        local_yaml.write_text(
            "model_list:\n  - model_name: scrum-po\n    litellm_params:\n      model: ollama/llama3.1:8b\n"
        )
        (tmp_path / ".env").write_text("OLLAMA_HOST_MODE='true'\n")
        assert run.compose_file_args(tmp_path) == ["-f", "docker-compose.local-hostollama.yaml"]

    def test_local_setup_with_host_mode_wins_over_gpu(self, tmp_path):
        """The two are mutually exclusive - host mode bypasses the
        dockerized `ollama` service entirely, so it takes priority if both
        were somehow set (setup_llm.py itself never writes both true)."""
        local_yaml = tmp_path / "config" / "model-templates" / "litellm.local-ollama.yaml"
        local_yaml.parent.mkdir(parents=True)
        local_yaml.write_text(
            "model_list:\n  - model_name: scrum-po\n    litellm_params:\n      model: ollama/llama3.1:8b\n"
        )
        (tmp_path / ".env").write_text("OLLAMA_HOST_MODE='true'\nOLLAMA_GPU_ENABLED='true'\n")
        assert run.compose_file_args(tmp_path) == ["-f", "docker-compose.local-hostollama.yaml"]


class TestWaitForHttp:
    def test_reachable_returns_true_immediately(self, ok_server):
        assert run.wait_for_http(ok_server, tries=5) is True

    def test_unreachable_returns_false_after_tries_exhausted(self, monkeypatch):
        # Avoid a real multi-second sleep in the test suite.
        monkeypatch.setattr(run.time, "sleep", lambda _seconds: None)
        assert run.wait_for_http("http://127.0.0.1:1", tries=3) is False


class _FakeDoctorResult:
    def __init__(self, has_errors):
        self.has_errors = has_errors


class _FakeThread:
    """Stands in for threading.Thread in main()-level tests: real Thread +
    the dashboard-opening background target would otherwise keep running
    past the mocks' monkeypatch teardown (it's never join()'d in the
    foreground/daemon startup paths tested here), leaking a background
    thread that then hits reverted mocks / real network calls and raises
    inside itself - reported by pytest as an unrelated
    PytestUnhandledThreadExceptionWarning. Making the thread a no-op
    entirely sidesteps that instead of racing against it."""
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def join(self, *args, **kwargs):
        pass


class TestMainDoctorGatekeeper:
    """
    Acceptance Criteria: doctor.py is the gatekeeper - run.py must not even
    attempt `docker compose up`/`run` if doctor.check() reports any
    ERROR-severity item, and must call it with skip_llm_probe=True (nothing
    is running yet, so a live proxy check here would only ever report "not
    reachable" and cost several real seconds for nothing).
    """

    def _common_mocks(self, monkeypatch):
        monkeypatch.setattr(run.os, "chdir", lambda _path: None)
        monkeypatch.setattr(run.shutil, "which", lambda _cmd: "/usr/bin/docker")
        monkeypatch.setattr(run, "wait_for_http", lambda *a, **k: True)
        monkeypatch.setattr(run.threading, "Thread", _FakeThread)

    def test_exits_before_docker_compose_when_doctor_reports_errors(self, monkeypatch):
        self._common_mocks(monkeypatch)
        monkeypatch.setattr(run.sys, "argv", ["run.py"])
        calls = []
        monkeypatch.setattr(run.doctor, "check", lambda *a, **k: calls.append(k) or _FakeDoctorResult(has_errors=True))

        def fail_if_called(*a, **k):
            raise AssertionError("docker compose must not be invoked when doctor reports errors")
        monkeypatch.setattr(run.subprocess, "run", fail_if_called)

        with pytest.raises(SystemExit) as exc_info:
            run.main()
        assert exc_info.value.code == 1
        assert calls[0].get("skip_llm_probe") is True

    def test_proceeds_when_doctor_reports_no_errors(self, monkeypatch):
        self._common_mocks(monkeypatch)
        monkeypatch.setattr(run.sys, "argv", ["run.py"])
        monkeypatch.setattr(run, "compose_file_args", lambda _root: [])
        monkeypatch.setattr(run.doctor, "check", lambda *a, **k: _FakeDoctorResult(has_errors=False))

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return run.subprocess.CompletedProcess(cmd, 0)
        monkeypatch.setattr(run.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            run.main()
        assert exc_info.value.code == 0
        assert captured["cmd"][:2] == ["docker", "compose"]


class TestMainKeyboardInterrupt:
    """
    Acceptance Criteria (GH issue #74): Ctrl+C during the foreground
    `docker compose up`/`run` call (subprocess.run's own wait, per a real
    Windows traceback) must produce a clean "Stopped." message and a
    non-error exit - not a raw KeyboardInterrupt traceback, which is
    exactly what both of this module's own "Press Ctrl+C to stop"
    messages promise is the normal way to end a foreground run.
    """

    def _common_mocks(self, monkeypatch):
        monkeypatch.setattr(run.os, "chdir", lambda _path: None)
        monkeypatch.setattr(run.shutil, "which", lambda _cmd: "/usr/bin/docker")
        monkeypatch.setattr(run, "wait_for_http", lambda *a, **k: True)
        monkeypatch.setattr(run.threading, "Thread", _FakeThread)
        monkeypatch.setattr(run, "compose_file_args", lambda _root: [])
        monkeypatch.setattr(run.doctor, "check", lambda *a, **k: _FakeDoctorResult(has_errors=False))

    def test_ctrl_c_during_foreground_web_mode_exits_cleanly(self, monkeypatch, capsys):
        self._common_mocks(monkeypatch)
        monkeypatch.setattr(run.sys, "argv", ["run.py"])

        def fake_run(cmd, **kwargs):
            raise KeyboardInterrupt()
        monkeypatch.setattr(run.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            run.main()
        assert exc_info.value.code == 0
        assert "Stopped." in capsys.readouterr().out

    def test_ctrl_c_during_cli_mode_exits_cleanly(self, monkeypatch, capsys):
        self._common_mocks(monkeypatch)
        monkeypatch.setattr(run.sys, "argv", ["run.py", "cli"])

        def fake_run(cmd, **kwargs):
            raise KeyboardInterrupt()
        monkeypatch.setattr(run.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            run.main()
        assert exc_info.value.code == 0
        assert "Stopped." in capsys.readouterr().out

    def test_ctrl_c_during_daemon_mode_exits_cleanly(self, monkeypatch, capsys):
        self._common_mocks(monkeypatch)
        monkeypatch.setattr(run.sys, "argv", ["run.py", "daemon"])

        def fake_run(cmd, **kwargs):
            raise KeyboardInterrupt()
        monkeypatch.setattr(run.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            run.main()
        assert exc_info.value.code == 0
        assert "Stopped." in capsys.readouterr().out


class TestMainDeveloperMode:
    """
    Acceptance Criteria: `python3 run.py dev` rebuilds agent/ollama images
    fresh before starting (see rebuild_images.rebuild) and runs with
    LOG_LEVEL=debug for that invocation, without needing that persisted to
    .env.
    """

    def _common_mocks(self, monkeypatch):
        monkeypatch.setattr(run.os, "chdir", lambda _path: None)
        monkeypatch.setattr(run.shutil, "which", lambda _cmd: "/usr/bin/docker")
        monkeypatch.setattr(run, "wait_for_http", lambda *a, **k: True)
        monkeypatch.setattr(run.threading, "Thread", _FakeThread)
        monkeypatch.setattr(run, "compose_file_args", lambda _root: [])
        monkeypatch.setattr(run.doctor, "check", lambda *a, **k: _FakeDoctorResult(has_errors=False))

    def test_dev_mode_rebuilds_before_starting_and_sets_debug_log_level(self, monkeypatch):
        self._common_mocks(monkeypatch)
        monkeypatch.setattr(run.sys, "argv", ["run.py", "dev"])

        rebuild_calls = []
        monkeypatch.setattr(run.rebuild_images, "rebuild", lambda compose_args, **k: rebuild_calls.append(compose_args) or 0)

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env")
            return run.subprocess.CompletedProcess(cmd, 0)
        monkeypatch.setattr(run.subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            run.main()

        assert exc_info.value.code == 0
        assert len(rebuild_calls) == 1
        assert captured["env"]["LOG_LEVEL"] == "debug"

    def test_dev_mode_rebuild_failure_stops_before_docker_compose_up(self, monkeypatch):
        self._common_mocks(monkeypatch)
        monkeypatch.setattr(run.sys, "argv", ["run.py", "dev"])
        monkeypatch.setattr(run.rebuild_images, "rebuild", lambda *a, **k: 1)

        def fail_if_called(*a, **k):
            raise AssertionError("docker compose up must not run if the dev-mode rebuild failed")
        monkeypatch.setattr(run.subprocess, "run", fail_if_called)

        with pytest.raises(SystemExit) as exc_info:
            run.main()
        assert exc_info.value.code == 1

    def test_non_dev_mode_does_not_rebuild_or_override_log_level(self, monkeypatch):
        self._common_mocks(monkeypatch)
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        monkeypatch.setattr(run.sys, "argv", ["run.py"])

        def fail_if_called(*a, **k):
            raise AssertionError("rebuild_images.rebuild must not be called outside developer mode")
        monkeypatch.setattr(run.rebuild_images, "rebuild", fail_if_called)

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return run.subprocess.CompletedProcess(cmd, 0)
        monkeypatch.setattr(run.subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            run.main()
        assert "LOG_LEVEL" not in captured["env"]


def _write_cloud_config(tmp_path, model="gemini/gemini-1.5-pro"):
    (tmp_path / "litellm.yaml").write_text(
        f"model_list:\n  - model_name: scrum-po\n    litellm_params:\n      model: {model}\n"
    )


def _write_local_config(tmp_path, model="ollama/llama3.1:8b"):
    """Writes the Local/Ollama config file with a newer mtime than any cloud
    config also written in the same test, so lib_llm_test.llm_active_config_path
    (mtime-based) reliably picks it - mirrors TestComposeFileArgs's setup."""
    local_yaml = tmp_path / "config" / "model-templates" / "litellm.local-ollama.yaml"
    local_yaml.parent.mkdir(parents=True, exist_ok=True)
    local_yaml.write_text(f"model_list:\n  - model_name: scrum-po\n    litellm_params:\n      model: {model}\n")
    future = time.time() + 10
    os.utime(local_yaml, (future, future))


class TestDetectExperimentalFeatures:
    """
    Acceptance Criteria (GH issue #263): run.py must be able to tell, from
    the launch mode and .env/litellm-config, which of the four experimental
    features flagged by GH issue #251 (Terminal UI, daemon mode, Local
    AI/Ollama, a non-"Product" Interaction Level) apply to this run.
    """

    def test_no_experimental_features_by_default(self, tmp_path):
        _write_cloud_config(tmp_path)
        assert run.detect_experimental_features(tmp_path, "web", False) == []

    def test_cli_mode_is_experimental(self, tmp_path):
        _write_cloud_config(tmp_path)
        features = run.detect_experimental_features(tmp_path, "cli", False)
        assert any("Terminal UI" in f for f in features)

    def test_daemon_mode_is_experimental(self, tmp_path):
        _write_cloud_config(tmp_path)
        features = run.detect_experimental_features(tmp_path, "web", True)
        assert any("Daemon mode" in f for f in features)

    def test_local_ollama_is_experimental(self, tmp_path):
        _write_local_config(tmp_path)
        features = run.detect_experimental_features(tmp_path, "web", False)
        assert any("Local AI" in f or "Ollama" in f for f in features)

    def test_cloud_provider_is_not_experimental(self, tmp_path):
        _write_cloud_config(tmp_path)
        features = run.detect_experimental_features(tmp_path, "web", False)
        assert not any("Ollama" in f for f in features)

    def test_non_product_interaction_level_is_experimental(self, tmp_path):
        _write_cloud_config(tmp_path)
        (tmp_path / ".env").write_text("INTERACTION_LEVEL='CEO'\n")
        features = run.detect_experimental_features(tmp_path, "web", False)
        assert any("CEO" in f for f in features)

    def test_product_interaction_level_is_not_experimental(self, tmp_path):
        _write_cloud_config(tmp_path)
        (tmp_path / ".env").write_text("INTERACTION_LEVEL='Product'\n")
        assert run.detect_experimental_features(tmp_path, "web", False) == []

    def test_missing_interaction_level_defaults_to_product_and_is_not_experimental(self, tmp_path):
        _write_cloud_config(tmp_path)
        assert run.detect_experimental_features(tmp_path, "web", False) == []

    def test_multiple_experimental_features_combine(self, tmp_path):
        _write_local_config(tmp_path)
        (tmp_path / ".env").write_text("OLLAMA_MODEL='llama3.1:8b'\nINTERACTION_LEVEL='Stakeholder'\n")
        features = run.detect_experimental_features(tmp_path, "cli", True)
        assert len(features) == 4


class TestBuildExperimentalIssueUrl:
    """
    Acceptance Criteria (GH issue #263): the warning banner's link must be a
    well-formed, properly URL-encoded GitHub "new issue" URL, prefilled via
    ?title=&body= with non-secret redacted config only (OS, VERSION,
    Interaction Level, active experimental features, provider/model name) -
    never an API key, token, or other secret-shaped .env value.
    """

    def test_url_targets_the_new_issue_page(self):
        url = run.build_experimental_issue_url(["Daemon mode"], "Product", "gemini", "gemini/gemini-1.5-pro")
        assert url.startswith(
            "https://github.com/CI-Till-Krempel/Horseless-Carriage/issues/new?"
        )

    def test_url_is_well_formed_and_parseable(self):
        url = run.build_experimental_issue_url(
            ["Daemon mode (`run.py daemon`)", "Interaction Level: CEO (non-Product)"],
            "CEO", "gemini", "gemini/gemini-1.5-pro",
        )
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        assert "title" in query and "body" in query
        body = query["body"][0]
        assert "CEO" in body
        assert "Daemon mode" in body
        assert "gemini" in body

    def test_url_includes_os_and_version(self):
        url = run.build_experimental_issue_url(["Daemon mode"], "Product", "gemini", "gemini/gemini-1.5-pro")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        body = query["body"][0]
        assert "OS:" in body
        assert run.banner.version() in body

    def test_url_excludes_secret_shaped_values(self, tmp_path):
        """Builds a fake .env with a fake API key, runs the real detection
        path against it, and asserts the resulting banner/URL never contains
        the secret - only the specific, known-safe fields (OS, version,
        Interaction Level, feature list, provider/model name) are ever
        read into the banner, so no full-.env dump can leak a key."""
        _write_cloud_config(tmp_path)
        secret = "sk-super-secret-key-should-never-leak-12345"
        (tmp_path / ".env").write_text(f"INTERACTION_LEVEL='CEO'\nGOOGLE_API_KEY='{secret}'\n")

        features = run.detect_experimental_features(tmp_path, "daemon", True)
        url = run.build_experimental_issue_url(features, "CEO", "gemini", "gemini/gemini-1.5-pro")

        assert secret not in url
        assert urllib.parse.quote(secret) not in url


class TestPrintExperimentalWarning:
    """
    Acceptance Criteria (GH issue #263): run.py must print a startup
    warning banner when the active configuration uses one or more
    experimental features (GH issue #251), and stay silent otherwise - a
    doc marker alone doesn't reach a user who's already past onboarding.
    """

    def test_prints_banner_for_daemon_plus_non_product_interaction_level(self, tmp_path, capsys):
        _write_cloud_config(tmp_path)
        (tmp_path / ".env").write_text("INTERACTION_LEVEL='CEO'\n")

        run.print_experimental_warning(tmp_path, "web", True)

        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "Daemon mode" in out
        assert "CEO" in out
        assert "https://github.com/CI-Till-Krempel/Horseless-Carriage/issues/new" in out

    def test_silent_when_no_experimental_features_active(self, tmp_path, capsys):
        _write_cloud_config(tmp_path)

        run.print_experimental_warning(tmp_path, "web", False)

        assert capsys.readouterr().out == ""

    def test_banner_excludes_secret_shaped_env_values(self, tmp_path, capsys):
        _write_local_config(tmp_path)
        secret = "sk-super-secret-key-should-never-leak-67890"
        (tmp_path / ".env").write_text(f"OLLAMA_MODEL='llama3.1:8b'\nOPENAI_API_KEY='{secret}'\n")

        run.print_experimental_warning(tmp_path, "cli", False)

        out = capsys.readouterr().out
        assert "WARNING" in out
        assert secret not in out
        assert urllib.parse.quote(secret) not in out
