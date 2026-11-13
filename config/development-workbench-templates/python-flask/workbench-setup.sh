#!/bin/sh
# Development Workbench: python-flask (US-0022, EP-0010)
#
# Toolchain for scaffolding, developing, testing, running, and debugging a
# Flask application inside the agent image. The base agent image
# (agent.Dockerfile, FROM python:3.11-slim) already provides Python 3.11 +
# pip, so this script only layers the Flask-stack-specific packages on top
# - the same "only add what the base image doesn't already have" shape
# every Development Workbench template follows.
set -e

pip install --no-cache-dir \
    flask \
    pytest \
    pytest-flask \
    debugpy \
    httpie

cat <<'EOF'
python-flask workbench ready: flask, pytest, pytest-flask, debugpy, httpie installed.

Scaffold (minimal app, from the project root):
  mkdir -p app && cat > app/__init__.py <<'PY'
  from flask import Flask

  app = Flask(__name__)

  @app.route("/")
  def index():
      return "ok"
  PY

Install project-declared deps:
  pip install -r requirements.txt

Test:
  pytest

Run:
  flask --app app run --debug --host=0.0.0.0

Debug (attach a remote debugger on port 5678):
  python -m debugpy --listen 0.0.0.0:5678 --wait-for-client \
      -m flask --app app run --no-debugger --no-reload

Exploratory testing:
  http GET http://127.0.0.1:5000/     # HTTPie against the running dev server
  flask --app app shell               # interactive REPL with full app context
EOF
