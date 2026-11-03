#!/usr/bin/env python3
"""
GH issue #310: a small always-on local web dashboard listing every
Horseless Carriage project registered on this machine (project_registry.py
- populated by new_project.py, GH issue #288) - name, config mode
(local/cloud), state (working/waiting for confirmation/resting),
interaction level, sprint goal, budget, product version, and assigned
LITELLM_HOST_PORT/AGENT_WEB_HOST_PORT - with manual start/stop per
project's docker stack. Starting a stopped project re-checks its ports are
still actually free and reassigns+persists (then rebuilds) if another
process has since taken one - see lib_docker.ensure_dev_ports_available.

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
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import lib_docker
import lib_env
import lib_llm_test
import project_registry

_STACK_ACTION_TIMEOUT_SECS = 300
_REACHABILITY_CHECK_TIMEOUT_SECS = 2


def _url_reachable(url: str, timeout_secs: float = _REACHABILITY_CHECK_TIMEOUT_SECS) -> bool:
    """One-shot (no retries) check that url answers with 2xx - unlike
    _start_stack's llm_wait_for_proxy wait-loop, this runs on every page
    render to show a live reachability indicator next to each project's
    LiteLLM/web UI links, not to gate starting the stack. A container
    reporting "running" (compose_running_services) only means the process
    inside it exists, not that the service it's supposed to serve has
    finished booting (or hasn't since crashed internally while the
    container stays up) - that gap is exactly why clicking a link next to
    a "working" project could still 404/refuse to connect."""
    try:
        with urllib.request.urlopen(url, timeout=timeout_secs) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


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
    registry_target_repo = Path(entry.get("target_repo", ""))

    if not hc_path.is_dir():
        return {"name": name, "hc_path": str(hc_path), "target_repo": str(registry_target_repo),
                "state": "missing", "error": f"{hc_path} no longer exists"}

    # project_registry.json's target_repo is a snapshot taken once, at
    # new_project.py install time (GH issue #310/#288) - the project's own
    # .env (STATE_REPO_PATH) is what the actual running agent container
    # uses, and is the only copy that can ever change after that (e.g. a
    # hand-edited .env, or an .env copied between two projects' submodule
    # checkouts to reuse API keys). A real incident: two different projects'
    # dashboard rows showed each other's sprint goal because one's STATE_
    # REPO_PATH, carried over this way, still pointed at the other's
    # directory - trusting the registry's cached value alone silently hid
    # this instead of surfacing it. Prefer the live .env value (what's
    # actually true right now) and flag the drift so it's visible, not
    # trust the install-time snapshot unconditionally.
    env_state_repo_path = lib_env.read_env_var(hc_path / ".env", "STATE_REPO_PATH")
    if env_state_repo_path:
        target_repo = Path(env_state_repo_path)
        if not target_repo.is_absolute():
            # Docker Compose resolves a relative STATE_REPO_PATH against the
            # compose file's own directory - that's hc_path, not wherever
            # dashboard.py itself happens to be running from.
            target_repo = hc_path / target_repo
    else:
        target_repo = registry_target_repo

    def _norm(p: Path) -> str:
        try:
            return str(p.resolve())
        except OSError:
            return str(p)

    target_repo_drifted = bool(env_state_repo_path) and _norm(target_repo) != _norm(registry_target_repo)
    if target_repo_drifted:
        # Self-heal, not just warn: .env is unconditionally the right answer
        # here (it's what the running agent actually uses), so there's no
        # legitimate case where the stale registry snapshot should win -
        # project_registry.sync_from_env re-derives name/target_repo from
        # this same .env, the same function setup_llm.py now also calls on
        # every reconfiguration.
        project_registry.sync_from_env(hc_path)

    try:
        config_mode = lib_llm_test.llm_active_provider(lib_llm_test.llm_active_config_path(hc_path))
    except Exception:
        config_mode = "unknown"

    interaction_level = lib_env.read_env_var(hc_path / ".env", "INTERACTION_LEVEL") or "Product"
    litellm_port = lib_env.read_env_var(hc_path / ".env", "LITELLM_HOST_PORT") or "4000"
    agent_web_port = lib_env.read_env_var(hc_path / ".env", "AGENT_WEB_HOST_PORT") or "8000"

    try:
        running_services = lib_docker.compose_running_services(_compose_args(hc_path), cwd=hc_path)
    except Exception:
        running_services = []
    is_running = bool(running_services)

    # Only probe when the stack is at least reporting as running - a
    # resting project is expected to be unreachable, not worth a network
    # round-trip (with its own timeout) on every 10s auto-refresh.
    litellm_reachable = is_running and _url_reachable(f"http://localhost:{litellm_port}/health/liveliness")
    web_reachable = is_running and _url_reachable(f"http://localhost:{agent_web_port}")

    state_data = _read_state_json(target_repo) if target_repo.is_dir() else {}
    blocking = [b for b in (state_data.get("blocking_interactions") or []) if not b.get("resolved")]
    sprint_goal = state_data.get("sprint_goal") or "(not set)"
    budgets = state_data.get("budgets") or {}
    token_usage = state_data.get("token_usage") or {}

    if not is_running:
        state = "resting"
    elif blocking:
        state = "waiting for confirmation"
    else:
        state = "working"

    # GH issue #454: the state badge alone gave no way to tell *what* is
    # being waited on or *where* to go answer it - surface the most recent
    # unresolved interaction's own summary instead of leaving it a dead end.
    blocking_summary = blocking[-1].get("summary", "") if blocking else ""

    product_version = _product_version(target_repo) if target_repo.is_dir() else "unknown"

    return {
        "name": name,
        "hc_path": str(hc_path),
        "target_repo": str(target_repo),
        "target_repo_drifted": target_repo_drifted,
        "config_mode": config_mode,
        "interaction_level": interaction_level,
        "state": state,
        "sprint_goal": sprint_goal,
        "token_usage": token_usage.get("total", 0),
        "token_budget": budgets.get("total", "unset"),
        "usd_budget": budgets.get("total_usd"),
        # GH issue #481: already computed and persisted by the live agent
        # session's own cost-budget callback (agent.py, check_cost_budget_
        # callback) - the dashboard previously just never read this field.
        "current_usd_spend": budgets.get("current_usd_spend"),
        "product_version": product_version,
        "litellm_port": litellm_port,
        "agent_web_port": agent_web_port,
        "litellm_reachable": litellm_reachable,
        "web_reachable": web_reachable,
        "blocking_summary": blocking_summary,
        "blocking_count": len(blocking),
        "error": None,
    }


def _start_stack(hc_path: Path) -> str:
    # GH issue #288's own port auto-assignment only ever ran once, at
    # new_project.py install time - re-check (and reassign+persist if
    # something else has since taken the configured port) right before this
    # project's stack actually comes up. A rebuild (--build) is only forced
    # when a port genuinely had to change (the image itself doesn't embed a
    # port, but a changed .env is exactly the kind of drift `docker compose
    # up` with no rebuild can silently serve stale against) - otherwise this
    # stays a plain, fast `up -d`.
    port_changes = lib_docker.ensure_dev_ports_available(hc_path, _compose_args(hc_path))
    cmd = ["docker", "compose", *_compose_args(hc_path), "up", "-d"]
    if port_changes:
        cmd.append("--build")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=hc_path, timeout=_STACK_ACTION_TIMEOUT_SECS)
    except Exception as e:
        return f"Failed to start: {e}"
    if result.returncode != 0:
        return f"Start failed:\n{result.stdout}\n{result.stderr}"

    # `docker compose up -d` only waits for the container to be *running*,
    # not for LiteLLM's own process inside it to have finished booting
    # (DB connection, config load, uvicorn startup) - a user clicking this
    # dashboard's own LiteLLM link right after "Started" otherwise hits a
    # proxy that isn't listening yet and looks like it's simply broken.
    # run.py already waits the same way before opening its own dashboard
    # link (wait_for_http against /health/readiness); reusing the shared
    # liveness-poll helper here instead of duplicating that loop.
    litellm_port = lib_env.read_env_var(hc_path / ".env", "LITELLM_HOST_PORT") or "4000"
    litellm_ready = lib_llm_test.llm_wait_for_proxy(f"http://localhost:{litellm_port}", timeout_secs=30)

    if port_changes:
        summary = ", ".join(f"{var} {old} -> {new}" for var, old, new in port_changes)
        message = f"Started (port(s) reassigned - {summary})."
    else:
        message = "Started."
    if not litellm_ready:
        message += (
            " LiteLLM didn't report ready within 30s - it may still be starting; wait a few more "
            "seconds before using the LiteLLM/web UI links."
        )
    return message


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


def _reachability_dot(reachable: bool) -> str:
    # GH issue tracker: a project shown as "working" gave no clue that its
    # LiteLLM/web UI link would actually 404/refuse to connect until a user
    # clicked it - surface reachability right next to the link instead.
    color = "#2e7d32" if reachable else "#c62828"
    title = "reachable" if reachable else "unreachable - link may not load"
    return f"<span style='color:{color}' title='{title}'>&#9679;</span>"


def _render_page(message: str = "") -> str:
    projects = project_registry.load()
    rows = []
    for entry in projects:
        snap = project_snapshot(entry)
        e = html.escape
        hc_path = snap["hc_path"]
        if snap.get("error"):
            rows.append(
                f"<tr><td>{e(snap['name'])}</td><td colspan='8'><em>{e(snap['error'])}</em></td></tr>"
            )
            continue
        color = _STATE_BADGE_COLOR.get(snap["state"], "#616161")
        action = (
            f"<a href='/stop?hc_path={e(hc_path)}'>Stop</a>"
            if snap["state"] != "resting"
            else f"<a href='/start?hc_path={e(hc_path)}'>Start</a>"
        )
        web_url = f"http://localhost:{snap['agent_web_port']}"
        state_html = f"<span style='color:{color};font-weight:bold'>{e(snap['state'])}</span>"
        if snap["state"] == "waiting for confirmation":
            # GH issue #454: starting a project whose stack comes up already
            # blocked on a human looked like an endless hang - there was no
            # indication of what it was waiting on or how to reach the chat
            # UI that can actually answer it. Say what's blocking, and link
            # straight to the web UI that can resolve it.
            count_suffix = f" (+{snap['blocking_count'] - 1} more)" if snap["blocking_count"] > 1 else ""
            state_html += (
                f"<br><small>{e(snap['blocking_summary'])}{count_suffix}<br>"
                f"<a href='{e(web_url)}' target='_blank'>Respond in web UI &rarr;</a></small>"
            )
        usd = f"${snap['usd_budget']:.2f}" if isinstance(snap.get("usd_budget"), (int, float)) and snap["usd_budget"] else "unset"
        spend = snap.get("current_usd_spend")
        spend_str = f"${spend:.2f} spent" if isinstance(spend, (int, float)) else "spend not yet available"
        sprint_goal_html = e(snap["sprint_goal"])
        if snap.get("target_repo_drifted"):
            # A real incident: project_registry.json's target_repo (set once,
            # at new_project.py install time) and this project's own live
            # .env STATE_REPO_PATH had diverged - e.g. an .env copied between
            # two projects' submodule checkouts to reuse API keys, carrying
            # over the OTHER project's STATE_REPO_PATH - so this row silently
            # showed the other project's sprint goal/state instead of its
            # own. project_snapshot already self-corrected the registry
            # entry to match .env by the time this renders - surface that
            # it HAD drifted (so a confusing prior screenshot/memory makes
            # sense) rather than leaving it silent now that it's fixed.
            sprint_goal_html += (
                f"<br><small style='color:#c62828'>&#9888; this project's registry entry had drifted "
                f"and was just corrected to {e(snap['target_repo'])} (from this project's own .env) - "
                "if that's not right, check STATE_REPO_PATH in this project's .env.</small>"
            )
        ports_html = (
            f"LiteLLM: <a href='http://localhost:{e(snap['litellm_port'])}/ui' target='_blank'>"
            f"{e(snap['litellm_port'])}</a>"
        )
        web_html = f"Web: <a href='{e(web_url)}' target='_blank'>{e(snap['agent_web_port'])}</a>"
        # A resting project is expected to be unreachable - a red dot there
        # would just be noise, not a signal of anything actually wrong.
        if snap["state"] != "resting":
            ports_html += f" {_reachability_dot(snap['litellm_reachable'])}"
            web_html += f" {_reachability_dot(snap['web_reachable'])}"
        rows.append(
            "<tr>"
            f"<td>{e(snap['name'])}</td>"
            f"<td>{state_html}</td>"
            f"<td>{e(snap['config_mode'])}</td>"
            f"<td>{e(snap['interaction_level'])}</td>"
            f"<td>{sprint_goal_html}</td>"
            f"<td>{snap['token_usage']:,} / {e(str(snap['token_budget']))} tokens, {e(spend_str)} / {usd} budget</td>"
            f"<td>{e(snap['product_version'])}</td>"
            f"<td>{ports_html} / {web_html}</td>"
            f"<td>{action}</td>"
            "</tr>"
        )
    if not rows:
        rows.append(
            "<tr><td colspan='9'><em>No projects registered yet - run "
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
<th>Sprint Goal</th><th>Budget</th><th>Product Version</th><th>Ports</th><th>Action</th></tr>
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

    # GH issue tracker ("old state" report): re-sync every registered
    # project from its own current .env, and drop any stale/bogus entry
    # (project_registry.sync_all's own docstring) before the first page is
    # ever rendered, rather than waiting for a row's lazy, per-render
    # self-heal to happen to run into drift.
    project_registry.sync_all()

    server = HTTPServer((args.bind_host, args.port), _DashboardHandler)
    print(f"Horseless Carriage dashboard: http://{args.bind_host}:{args.port}")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
