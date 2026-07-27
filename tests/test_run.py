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


class TestWaitForHttp:
    def test_reachable_returns_true_immediately(self, ok_server):
        assert run.wait_for_http(ok_server, tries=5) is True

    def test_unreachable_returns_false_after_tries_exhausted(self, monkeypatch):
        # Avoid a real multi-second sleep in the test suite.
        monkeypatch.setattr(run.time, "sleep", lambda _seconds: None)
        assert run.wait_for_http("http://127.0.0.1:1", tries=3) is False
