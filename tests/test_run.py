import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import run


class TestParseArgs:
    def test_defaults(self):
        assert run.parse_args([]) == ("web", False, [])

    def test_cli_mode_with_query_args(self):
        assert run.parse_args(["cli", "hello", "world"]) == ("cli", False, ["hello", "world"])

    def test_daemon_flag(self):
        assert run.parse_args(["daemon"]) == ("web", True, [])

    def test_cli_and_daemon_combined(self):
        assert run.parse_args(["cli", "daemon", "query"]) == ("cli", True, ["query"])

    def test_last_mode_keyword_wins(self):
        # Mirrors the bash version's plain for-loop scan: later keywords override earlier ones.
        assert run.parse_args(["cli", "web"]) == ("web", False, [])


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


class TestWaitForHttp:
    def test_reachable_returns_true_immediately(self, ok_server):
        assert run.wait_for_http(ok_server, tries=5) is True

    def test_unreachable_returns_false_after_tries_exhausted(self, monkeypatch):
        # Avoid a real multi-second sleep in the test suite.
        monkeypatch.setattr(run.time, "sleep", lambda _seconds: None)
        assert run.wait_for_http("http://127.0.0.1:1", tries=3) is False
