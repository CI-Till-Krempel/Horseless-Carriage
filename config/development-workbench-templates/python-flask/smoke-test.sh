#!/usr/bin/env bash
# Smoke test for the python-flask Development Workbench template (US-0022).
#
# NOT run by `python3 run_tests.py` / `pytest agents/scrum_team/tests`: that
# suite runs inside the `agent` Docker Compose service, which sits on
# agent-net (internal, egress restricted to github.com/api.github.com by
# net-proxy's allowlist - see docker-compose.yaml and
# config/net-proxy/allowlist.filter). This script needs real internet
# access to pypi.org/files.pythonhosted.org to install Flask, which that
# sandboxed test container deliberately cannot reach - the whole point of
# that allowlist.
#
# Run this manually, or wire it into a dedicated CI job that runs on a
# normal (non-sandboxed) runner with internet access - e.g. a separate step
# in .github/workflows/ci.yml, NOT routed through net-proxy:
#   config/development-workbench-templates/python-flask/smoke-test.sh
#
# What it does: builds a throwaway image using this template's own
# workbench-setup.sh (the same COPY+RUN agent.Dockerfile performs for a
# real project's .hc/workbench-setup.sh), scaffolds a minimal Flask app
# inside it, and runs that scaffolded app's own pytest suite - proving the
# template's install/scaffold/test path works end to end, not just that
# its files parse.
set -euo pipefail
cd "$(dirname "$0")"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

cat > "$WORK/Dockerfile" <<'DOCKERFILE'
FROM python:3.11-slim
WORKDIR /app
COPY workbench-setup.sh /tmp/workbench-setup.sh
RUN chmod +x /tmp/workbench-setup.sh && /tmp/workbench-setup.sh
DOCKERFILE
cp workbench-setup.sh "$WORK/workbench-setup.sh"

docker build -t hc-smoke-python-flask "$WORK"

docker run --rm hc-smoke-python-flask sh -c '
  set -e
  mkdir -p /tmp/app && cd /tmp/app
  cat > app.py <<PY
from flask import Flask

app = Flask(__name__)


@app.route("/")
def index():
    return "ok"
PY
  cat > test_app.py <<PY
from app import app


def test_index():
    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.data == b"ok"
PY
  pytest -q
'
echo "python-flask smoke test passed: scaffolded app installed, tested, and ran successfully."
