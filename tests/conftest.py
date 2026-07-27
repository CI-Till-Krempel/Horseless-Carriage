"""
Shared pytest fixtures for the host-side script tests (lib_env, lib_llm_test,
setup_llm, doctor, check_state_repo, run). These tests run directly on the
host - no Docker required - which is exactly the point of the Python rewrite
(setup_llm.py etc. must work before any container exists).
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

# The scripts under test (lib_env.py, setup_llm.py, ...) live at the repo
# root, one level up from this tests/ directory - make them importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class _MockLiteLLMHandler(BaseHTTPRequestHandler):
    """A minimal stand-in for the LiteLLM proxy's /health/liveliness and
    /chat/completions endpoints, configurable per-test via `behavior`."""

    behavior = {"valid_key": "good-key", "missing_model": None}

    def log_message(self, *args):  # silence request logging during tests
        pass

    def do_GET(self):
        if self.path.startswith("/health/liveliness"):
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        auth = self.headers.get("Authorization", "")
        if auth != f"Bearer {self.behavior['valid_key']}":
            self._json(401, {"error": {"message": "Invalid API key"}})
            return
        if self.behavior.get("missing_model") and body.get("model") == self.behavior["missing_model"]:
            self._json(404, {"error": {"message": "model not found"}})
            return
        self._json(200, {"choices": [{"message": {"content": "OK"}}]})

    def _json(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def mock_proxy():
    """Yields (base_url, behavior_dict). Mutate behavior_dict in a test to
    control auth/model-not-found responses before making requests."""
    _MockLiteLLMHandler.behavior = {"valid_key": "good-key", "missing_model": None}
    server = HTTPServer(("127.0.0.1", 0), _MockLiteLLMHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", _MockLiteLLMHandler.behavior
    finally:
        server.shutdown()
        thread.join(timeout=5)
