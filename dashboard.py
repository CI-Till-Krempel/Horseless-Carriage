#!/usr/bin/env python3
"""
GH issue #310: a small always-on local web dashboard listing every
Horseless Carriage project registered on this machine (project_registry.py
- populated by new_project.py, GH issue #288) - name, config mode
(local/cloud), state (working/waiting for confirmation/resting),
interaction level, sprint goal, budget, and product version - with manual
start/stop per project's docker stack.

Minimal first cut, per GH issue #310's own decision comment: reads each
project's cached `.hc/state.json` and `.env` (and, only while that
project's stack happens to be running, which containers are up) rather
than polling each project's live LiteLLM proxy for current spend. State
is derived from existing data (ScrumState.blocking_interactions + whether
the stack is running), not a new field the agent loop has to maintain.

Stdlib-only (http.server), matching every other host-side script in this
repo - no Flask/new dependency for a page this simple.

Usage:
  python3 dashboard.py [--port 8899] [--bind-host 127.0.0.1]
"""
import argparse
import html
import json
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import lib_docker
import lib_env
import lib_llm_test
import project_registry

_STACK_ACTION_TIMEOUT_SECS = 300


def _read_state_json(target_repo: Path) -> dict:
    state_path = target_repo / ".hc" / "state.json"
    if not state_path.is_file():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _product_version(target_repo: Path) -> str:
    version_file = target_repo / "VERSION"
    if version_file.is_file():
        text = version_file.read_text(encoding="utf-8").strip()
        if text:
            return text
    result = subprocess.run(
        ["git", "-C", str(target_repo), "describe", "--tags", "--always"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "unknown"


def _compose_args(hc_path: Path) -> list:
    return lib_docker.compose_file_args(hc_path) + lib_docker.compose_project_args("dev", hc_path)


def project_snapshot(entry: dict) -> dict:
    """Everything the dashboard renders for one registered project - never
    raises: a project whose hc_path/target_repo has since been deleted, or
    whose docker/git calls fail, still gets a row (marked accordingly)
    instead of taking the whole dashboard down."""
    name = entry.get("name", "(unnamed)")
    hc_path = Path(entry.get("hc_path", ""))
    target_repo = Path(entry.get("target_repo", ""))

    if not hc_path.is_dir():
        return {"name": name, "hc_path": str(hc_path), "target_repo": str(target_repo),
                "state": "missing", "error": f"{hc_path} no longer exists"}

    try:
        config_mode = lib_llm_test.llm_active_provider(lib_llm_test.llm_active_config_path(hc_path))
    except Exception:
        config_mode = "unknown"

    interaction_level = lib_env.read_env_var(hc_path / ".env", "INTERACTION_LEVEL") or "Product"

    try:
        running_services = lib_docker.compose_running_services(_compose_args(hc_path), cwd=hc_path)
    except Exception:
        running_services = []
    is_running = bool(running_services)

    state_data = _read_state_json(target_repo) if target_repo.is_dir() else {}
    blocking = state_data.get("blocking_interactions") or []
    sprint_goal = state_data.get("sprint_goal") or "(not set)"
    budgets = state_data.get("budgets") or {}
    token_usage = state_data.get("token_usage") or {}

    if not is_running:
        state = "resting"
    elif blocking:
        state = "waiting for confirmation"
    else:
        state = "working"

    product_version = _product_version(target_repo) if target_repo.is_dir() else "unknown"

    return {
        "name": name,
        "hc_path": str(hc_path),
        "target_repo": str(target_repo),
        "config_mode": config_mode,
        "interaction_level": interaction_level,
        "state": state,
        "sprint_goal": sprint_goal,
        "token_usage": token_usage.get("total", 0),
        "token_budget": budgets.get("total", "unset"),
        "usd_budget": budgets.get("total_usd"),
        "product_version": product_version,
        "error": None,
    }


def _start_stack(hc_path: Path) -> str:
    cmd = ["docker", "compose", *_compose_args(hc_path), "up", "-d"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=hc_path, timeout=_STACK_ACTION_TIMEOUT_SECS)
    except Exception as e:
        return f"Failed to start: {e}"
    return "Started." if result.returncode == 0 else f"Start failed:\n{result.stdout}\n{result.stderr}"


def _stop_stack(hc_path: Path) -> str:
    cmd = ["docker", "compose", *_compose_args(hc_path), "down"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=hc_path, timeout=_STACK_ACTION_TIMEOUT_SECS)
    except Exception as e:
        return f"Failed to stop: {e}"
    return "Stopped." if result.returncode == 0 else f"Stop failed:\n{result.stdout}\n{result.stderr}"


_STATE_BADGE_COLOR = {
    "working": "#2e7d32",
    "waiting for confirmation": "#e65100",
    "resting": "#616161",
    "missing": "#c62828",
}


def _render_page(message: str = "") -> str:
    projects = project_registry.load()
    rows = []
    for entry in projects:
        snap = project_snapshot(entry)
        e = html.escape
        hc_path = snap["hc_path"]
        if snap.get("error"):
            rows.append(
                f"<tr><td>{e(snap['name'])}</td><td colspan='7'><em>{e(snap['error'])}</em></td></tr>"
            )
            continue
        color = _STATE_BADGE_COLOR.get(snap["state"], "#616161")
        action = (
            f"<a href='/stop?hc_path={e(hc_path)}'>Stop</a>"
            if snap["state"] != "resting"
            else f"<a href='/start?hc_path={e(hc_path)}'>Start</a>"
        )
        usd = f"${snap['usd_budget']:.2f}" if isinstance(snap.get("usd_budget"), (int, float)) and snap["usd_budget"] else "unset"
        rows.append(
            "<tr>"
            f"<td>{e(snap['name'])}</td>"
            f"<td><span style='color:{color};font-weight:bold'>{e(snap['state'])}</span></td>"
            f"<td>{e(snap['config_mode'])}</td>"
            f"<td>{e(snap['interaction_level'])}</td>"
            f"<td>{e(snap['sprint_goal'])}</td>"
            f"<td>{snap['token_usage']:,} / {e(str(snap['token_budget']))} tokens, {usd}</td>"
            f"<td>{e(snap['product_version'])}</td>"
            f"<td>{action}</td>"
            "</tr>"
        )
    if not rows:
        rows.append(
            "<tr><td colspan='8'><em>No projects registered yet - run "
            "<code>python3 new_project.py --target-repo /path/to/your-project</code> to add one.</em></td></tr>"
        )
    message_html = f"<p style='color:#1565c0'>{html.escape(message)}</p>" if message else ""
    return f"""<!doctype html>
<html><head><meta http-equiv="refresh" content="10">
<title>Horseless Carriage - Projects</title>
<style>
body {{ font-family: sans-serif; margin: 2rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 0.5rem; text-align: left; vertical-align: top; }}
th {{ background: #f0f0f0; }}
</style>
</head><body>
<h1>Horseless Carriage - Projects</h1>
{message_html}
<table>
<tr><th>Name</th><th>State</th><th>Config</th><th>Interaction Level</th>
<th>Sprint Goal</th><th>Budget</th><th>Product Version</th><th>Action</th></tr>
{"".join(rows)}
</table>
<p><small>Auto-refreshes every 10s. Registry: {html.escape(str(project_registry.REGISTRY_PATH))}</small></p>
</body></html>
"""


class _DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quieter than the default per-request stderr log
        pass

    def _send_html(self, body: str, code: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect_to_root(self, message: str) -> None:
        self.send_response(303)
        self.send_header("Location", f"/?msg={html.escape(message)}")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/":
            self._send_html(_render_page(query.get("msg", [""])[0]))
        elif parsed.path in ("/start", "/stop"):
            hc_path = query.get("hc_path", [""])[0]
            # Only ever act on a path that's actually in the registry - the
            # query param alone must not be enough to make this endpoint
            # run `docker compose` against an arbitrary local directory.
            registered_paths = {entry.get("hc_path") for entry in project_registry.load()}
            if hc_path not in registered_paths:
                message = "Not a registered project."
            elif parsed.path == "/start":
                message = _start_stack(Path(hc_path))
            else:
                message = _stop_stack(Path(hc_path))
            self._redirect_to_root(message)
        else:
            self._send_html("Not found", code=404)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument(
        "--bind-host", type=str, default="127.0.0.1",
        help="Bind address (default 127.0.0.1 - no authentication in front of this, same rationale as "
             "the ADK web UI/LiteLLM proxy - GH issue #239). Only change this on a network you trust.",
    )
    args = parser.parse_args()

    server = HTTPServer((args.bind_host, args.port), _DashboardHandler)
    print(f"Horseless Carriage dashboard: http://{args.bind_host}:{args.port}")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
