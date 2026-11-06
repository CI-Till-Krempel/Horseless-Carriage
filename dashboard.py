#!/usr/bin/env python3
"""
GH issue #310: a small always-on local web dashboard listing every
Horseless Carriage project registered on this machine (project_registry.py
- populated by new_project.py, GH issue #288) - name, config mode
(local/cloud), state (working/waiting for confirmation/resting), current
sprint phase and ticket, interaction level, sprint goal, budget, product
version, and assigned LITELLM_HOST_PORT/AGENT_WEB_HOST_PORT - with manual
start/stop per project's docker stack. Starting a stopped project
re-checks its ports are still actually free and reassigns+persists (then
rebuilds) if another process has since taken one - see
lib_docker.ensure_dev_ports_available.

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
import contextlib
import html
import io
import json
import subprocess
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import doctor
import lib_docker
import lib_env
import lib_github
import lib_llm_test
import project_registry
import update_hc
from agents.scrum_team.helpers import current_sprint_phase

_STACK_ACTION_TIMEOUT_SECS = 300
_REACHABILITY_CHECK_TIMEOUT_SECS = 2
_RELEASE_CACHE_TTL_SECS = 60


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


def _doctor_items(hc_path: Path) -> list:
    """doctor.py's own check(), in dry-run (writes nothing) + network-
    skipping mode - this runs on every 10s auto-refresh for every
    registered project, so it must never mutate the checkout (dry_run=True
    - see check()'s own docstring for exactly which steps that skips) or
    pay for live GitHub/LLM-proxy/Ollama network calls (skip_llm_probe=
    True) just to paint a status badge. check() also prints a full
    human-readable report to stdout on every call (meant for a one-off
    `python3 doctor.py` run from a terminal) - redirected away here so it
    doesn't spam this server's own console on every render for every
    project."""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = doctor.check(hc_path, skip_llm_probe=True, dry_run=True)
    except Exception:
        return []
    return [{"severity": item.severity, "message": item.message} for item in result.items]


def _norm_path(p: Path) -> str:
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


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


def _git_short_commit(repo_path: Path) -> str:
    """Short HEAD commit hash of the git checkout at repo_path - used for
    both hc_commit (hc_path, this project's HC checkout - distinct from
    hc_version, the VERSION file contents recorded into state.json from
    *inside* the agent container, where .git is deliberately excluded from
    the image, see .dockerignore/GH issue #123 and base.py's _hc_version
    docstring) and, as a fallback, product_commit (target_repo, when it has
    no GitHub release yet to read a commit from instead). dashboard.py
    itself runs on the host, where both repos' .git are directly available,
    so this reads it straight from there - same host-side `git rev-parse`
    pattern as run_adk_eval.py's hc_version_and_commit - rather than needing
    an HC_COMMIT_SHA env var."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


_release_cache = {}


def _cached_latest_release(owner: str, repo: str, env: dict) -> dict:
    """lib_github.latest_release(), cached per (owner, repo) for
    _RELEASE_CACHE_TTL_SECS. This page auto-refreshes every 10s (see
    _render_page's own <meta refresh>) for every registered project, and
    _doctor_items already runs doctor.check() in skip_llm_probe=True mode
    specifically to avoid paying for a live GitHub network call on every one
    of those refreshes (see its own docstring) - querying GitHub's releases
    API that often would reintroduce exactly the cost that guards against,
    and risks tripping GitHub's rate limit. Token resolution (which, for a
    GitHub App, itself mints a token over the network) is likewise only
    attempted on a cache miss, not on every render."""
    key = (owner, repo)
    now = time.time()
    cached = _release_cache.get(key)
    if cached and now - cached[0] < _RELEASE_CACHE_TTL_SECS:
        return cached[1]
    token, _source = lib_github.resolve_token(env)
    release = lib_github.latest_release(owner, repo, token) if token else None
    _release_cache[key] = (now, release)
    return release


def _product_version_and_commit(target_repo: Path, owner_repo, hc_env: dict) -> tuple:
    """(version, short_commit) for the Product Version column, in the same
    "X.Y.Z (abcdef1)" display format as hc_version/hc_commit. Prefers the
    latest published GitHub release (what actually shipped) over the
    checkout's local VERSION file/HEAD when a release exists and GitHub
    access is configured, since the checkout can be ahead of the last
    release (e.g. mid-sprint commits not yet released) - falls back to the
    local VERSION file + target_repo's own HEAD otherwise (no release yet,
    or no GitHub access configured)."""
    if owner_repo:
        release = _cached_latest_release(owner_repo[0], owner_repo[1], hc_env)
        if release and release.get("tag_name"):
            commit_sha = release.get("commit_sha")
            # Tags are cut as "vX.Y.Z" (agents/scrum_team/tools/github.py's
            # gh_release_create) but the VERSION file - and this column,
            # pre-release - has always read "X.Y.Z" with no "v"; strip it so
            # the column doesn't change shape the moment a project ships
            # its first release.
            version = release["tag_name"][1:] if release["tag_name"].startswith("v") else release["tag_name"]
            return version, (commit_sha[:8] if commit_sha else None)

    version = _product_version(target_repo) if target_repo.is_dir() else "unknown"
    commit = _git_short_commit(target_repo) if target_repo.is_dir() else None
    return version, commit


def _compose_args(hc_path: Path) -> list:
    return lib_docker.compose_file_args(hc_path) + lib_docker.compose_project_args("dev", hc_path)


def _current_ticket_label(state_data: dict) -> str:
    """The story the team is currently working, for the status bar's "Ticket:
    ..." line - a coarser, always-available substitute for the one-off
    blocking_summary text (which only ever said anything while a specific
    interaction was unresolved). agents.scrum_team.tools.requirements can't
    be imported at module level here: importing it runs its package's
    tools/__init__.py, which eagerly imports tools/budget.py's `import
    requests` - a dependency only installed inside the agent's own docker
    image, not on the host running this stdlib-only dashboard (same
    constraint lib_github.py's auth_github import works around)."""
    try:
        from agents.scrum_team.tools.requirements import _current_story_in_progress
    except Exception:
        return ""
    product_backlog = state_data.get("product_backlog") or []
    item = _current_story_in_progress(product_backlog)
    if not item:
        return ""
    story_id = item.get("id")
    title = item.get("title")
    if story_id and title:
        return f"{story_id}: {title}"
    return story_id or title or ""


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

    target_repo_drifted = bool(env_state_repo_path) and _norm_path(target_repo) != _norm_path(registry_target_repo)
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

    # GITHUB_REPO_URL lives in this HC installation's own .env (same one
    # new_project.py/setup_llm.py write it to) - not the target repo's, which
    # is why this reads hc_path, not target_repo.
    github_repo_url = lib_env.read_env_var(hc_path / ".env", "GITHUB_REPO_URL")
    owner_repo = lib_github.parse_owner_repo(github_repo_url)
    github_prs_url = f"https://github.com/{owner_repo[0]}/{owner_repo[1]}/pulls" if owner_repo else None

    doctor_items = _doctor_items(hc_path)

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

    # GH issue tracker ("dashboard doesn't update" report): `docker compose
    # up` only recreates a container when Compose notices the resolved
    # service config actually changed - a container already running from
    # BEFORE STATE_REPO_PATH was repointed keeps the OLD bind mount it was
    # created with. Trusting the .env value resolved above would then read
    # a directory the live agent never writes to at all - forever stale, no
    # matter how many times this re-renders, because the real data is
    # landing somewhere else entirely. lib_docker.running_state_repo_mount
    # asks Docker what /app/state_repo is actually bind-mounted to right
    # now (doctor.py already uses this same call to warn about exactly this
    # before a repoint-recreate would orphan a different project) - when it
    # disagrees with the configured path, that live container is the one
    # actually producing new sprint-goal/message updates, so read from
    # there instead of the configured-but-currently-inactive path.
    configured_target_repo = target_repo
    state_repo_mount_drifted = False
    if is_running:
        try:
            running_mount = lib_docker.running_state_repo_mount(_compose_args(hc_path), cwd=hc_path)
        except Exception:
            running_mount = None
        if running_mount:
            running_target_repo = Path(lib_docker.normalize_docker_mount_source(running_mount))
            if _norm_path(running_target_repo) != _norm_path(configured_target_repo):
                state_repo_mount_drifted = True
                target_repo = running_target_repo

    state_data = _read_state_json(target_repo) if target_repo.is_dir() else {}
    blocking = [b for b in (state_data.get("blocking_interactions") or []) if not b.get("resolved")]
    sprint_goal = state_data.get("sprint_goal") or "(not set)"
    sprint_number = state_data.get("sprint_number")
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

    # The state badge (working/waiting for confirmation/resting) is a coarse
    # 3-way health signal, and blocking_summary only ever says anything while
    # a specific interaction is unresolved - neither tells a user what the
    # team is actually doing right now. Surface the same sprint phase/current
    # ticket the live agent session already tracks for itself (agent.py's
    # console-log prefix), computed fresh from state.json on every render so
    # it's never stale the way an orphaned blocking_interaction can be.
    try:
        sprint_phase = current_sprint_phase(state_data) if state_data else ""
    except Exception:
        sprint_phase = ""
    try:
        current_ticket = _current_ticket_label(state_data) if state_data else ""
    except Exception:
        current_ticket = ""

    hc_env = lib_env.load_env_file(hc_path / ".env")
    product_version, product_commit = _product_version_and_commit(target_repo, owner_repo, hc_env)
    # agents/scrum_team/tools/scrum.py's init_scrum_state sets this to
    # _hc_version() (the Horseless Carriage checkout's own VERSION file) on
    # every session, persisted into REPO_STATE_KEYS alongside everything
    # else - see RELEASE.md's "Tracking the HC version in the state repo".
    # Distinct from product_version above (the TARGET repo's own version) -
    # this is which version of the tooling itself actually produced this
    # sprint's data, independent of which product it's driving.
    hc_version = state_data.get("hc_version") or "unknown"
    # GH issue: the dashboard showed hc_version alone (e.g. "1.0.0"), which
    # doesn't pin down *which* commit at that version actually produced a
    # given sprint's data between releases - hc_commit is this project's HC
    # checkout's current commit (read host-side, see _git_short_commit),
    # displayed alongside it as "1.0.0 (e83912ed)".
    hc_commit = _git_short_commit(hc_path) if hc_path.is_dir() else None

    return {
        "name": name,
        "hc_path": str(hc_path),
        "target_repo": str(target_repo),
        "target_repo_drifted": target_repo_drifted,
        "state_repo_mount_drifted": state_repo_mount_drifted,
        "configured_target_repo": str(configured_target_repo),
        "config_mode": config_mode,
        "interaction_level": interaction_level,
        "state": state,
        "sprint_phase": sprint_phase,
        "current_ticket": current_ticket,
        "sprint_goal": sprint_goal,
        "sprint_number": sprint_number,
        "github_prs_url": github_prs_url,
        "doctor_items": doctor_items,
        "token_usage": token_usage.get("total", 0),
        "token_budget": budgets.get("total", "unset"),
        "usd_budget": budgets.get("total_usd"),
        # GH issue #481: already computed and persisted by the live agent
        # session's own cost-budget callback (agent.py, check_cost_budget_
        # callback) - the dashboard previously just never read this field.
        "current_usd_spend": budgets.get("current_usd_spend"),
        "product_version": product_version,
        "product_commit": product_commit,
        "hc_version": hc_version,
        "hc_commit": hc_commit,
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


def _doctor_badge(doctor_items: list) -> str:
    """A compact "is this project's setup healthy" summary - doctor.py was
    previously something you had to remember to run by hand; this surfaces
    the same punch list right on the row it's about. <details>/<summary>
    rather than a tooltip: a tooltip can't hold a multi-item list legibly,
    and this needs no JS to expand."""
    e = html.escape
    errors = [i for i in doctor_items if i["severity"] == "error"]
    warnings = [i for i in doctor_items if i["severity"] == "warning"]
    if not errors and not warnings:
        return "<span style='color:#2e7d32'>&#10003; OK</span>"
    parts = []
    if errors:
        parts.append(f"{len(errors)} error(s)")
    if warnings:
        parts.append(f"{len(warnings)} warning(s)")
    color = "#c62828" if errors else "#e65100"
    items_html = "".join(f"<li>[{i['severity'].upper()}] {e(i['message'])}</li>" for i in doctor_items)
    return (
        f"<details><summary style='color:{color};cursor:pointer'>&#9888; {', '.join(parts)}</summary>"
        f"<ul style='margin:0.25rem 0 0 1rem;padding:0'>{items_html}</ul></details>"
    )


def _render_page(message: str = "") -> str:
    projects = project_registry.load()
    rows = []
    for entry in projects:
        snap = project_snapshot(entry)
        e = html.escape
        hc_path = snap["hc_path"]
        if snap.get("error"):
            rows.append(
                f"<tr><td>{e(snap['name'])}</td><td colspan='11'><em>{e(snap['error'])}</em></td></tr>"
            )
            continue
        color = _STATE_BADGE_COLOR.get(snap["state"], "#616161")
        action = (
            f"<a href='/stop?hc_path={e(hc_path)}'>Stop</a>"
            if snap["state"] != "resting"
            else f"<a href='/start?hc_path={e(hc_path)}'>Start</a>"
        )
        # GH issue: updating an installed checkout used to mean a hand-rolled
        # `git -C horseless-carriage fetch && git checkout ...` dance outside
        # this dashboard entirely - these call update_hc.run_update, which
        # stops the stack first (if running), updates, rebuilds images if the
        # update actually warrants it, and restarts (if it was running)
        # before this redirects back here.
        action += (
            f"<br><a href='/update?hc_path={e(hc_path)}&channel=release'>Update (latest release)</a>"
            f"<br><a href='/update?hc_path={e(hc_path)}&channel=main'>Update (main)</a>"
        )
        web_url = f"http://localhost:{snap['agent_web_port']}"
        state_html = f"<span style='color:{color};font-weight:bold'>{e(snap['state'])}</span>"
        # The badge word alone (and, while a human review is pending, the
        # specific blocking_summary below) doesn't say what the team is
        # actually doing right now - a coarser, always-on "what phase, which
        # ticket" line reads less alarming than a stuck-looking one-word
        # state, without the noise of e.g. every individual tool call.
        phase_ticket_bits = []
        if snap.get("sprint_phase"):
            phase_ticket_bits.append(f"Phase: {e(snap['sprint_phase'])}")
        if snap.get("current_ticket"):
            phase_ticket_bits.append(f"Ticket: {e(snap['current_ticket'])}")
        if phase_ticket_bits:
            state_html += f"<br><small>{' &middot; '.join(phase_ticket_bits)}</small>"
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
        if snap.get("sprint_number") is not None:
            sprint_goal_html = f"<strong>Sprint {e(str(snap['sprint_number']))}:</strong> {sprint_goal_html}"
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
        if snap.get("state_repo_mount_drifted"):
            # GH issue tracker ("dashboard doesn't update" report): `docker
            # compose up` only recreates a container when Compose notices
            # the resolved config changed - a container started BEFORE
            # STATE_REPO_PATH was repointed keeps running against its OLD
            # bind mount until it's explicitly recreated. The sprint
            # goal/latest message above are being read from that container's
            # actual mount (the only place still getting real updates), not
            # from .env's current value - without this, the row just looked
            # permanently frozen with no indication why.
            sprint_goal_html += (
                f"<br><small style='color:#c62828'>&#9888; this project's running container is still "
                f"serving {e(snap['target_repo'])}, not {e(snap['configured_target_repo'])} (.env's current "
                "STATE_REPO_PATH) - showing that container's real data above. Restart this project (Stop, "
                "then Start) to pick up the configured change.</small>"
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
        github_html = (
            f"<a href='{e(snap['github_prs_url'])}' target='_blank'>PRs &rarr;</a>"
            if snap.get("github_prs_url") else "<em>not configured</em>"
        )
        product_version_html = e(snap["product_version"])
        if snap.get("product_commit"):
            product_version_html += f" ({e(snap['product_commit'])})"
        hc_version_html = e(snap["hc_version"])
        if snap.get("hc_commit"):
            hc_version_html += f" ({e(snap['hc_commit'])})"
        rows.append(
            "<tr>"
            f"<td>{e(snap['name'])}</td>"
            f"<td>{state_html}</td>"
            f"<td>{e(snap['config_mode'])}</td>"
            f"<td>{e(snap['interaction_level'])}</td>"
            f"<td>{sprint_goal_html}</td>"
            f"<td>{snap['token_usage']:,} / {e(str(snap['token_budget']))} tokens, {e(spend_str)} / {usd} budget</td>"
            f"<td>{product_version_html}</td>"
            f"<td>{hc_version_html}</td>"
            f"<td>{github_html}</td>"
            f"<td>{_doctor_badge(snap['doctor_items'])}</td>"
            f"<td>{ports_html} / {web_html}</td>"
            f"<td>{action}</td>"
            "</tr>"
        )
    if not rows:
        rows.append(
            "<tr><td colspan='12'><em>No projects registered yet - run "
            "<code>python3 new_project.py --target-repo /path/to/your-project</code> to add one.</em></td></tr>"
        )
    # update_hc.run_update's own messages are routinely multi-line (one line
    # per step, e.g. "Stopped." / "Updated v0.1.0 -> v0.2.0." / "Started.") -
    # preserve that as <br>s rather than squashing it into one unreadable line.
    message_html = (
        f"<p style='color:#1565c0;white-space:pre-line'>{html.escape(message)}</p>" if message else ""
    )
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
<th>Sprint Goal</th><th>Budget</th><th>Product Version</th><th>HC Version</th><th>GitHub</th>
<th>Doctor</th><th>Ports</th><th>Action</th></tr>
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
        # update_hc.run_update's own messages are routinely multi-line (one
        # line per step) - quote() (not html.escape, which leaves newlines
        # and '&' as literal bytes) so a multi-line message can't break the
        # Location header's own line framing or get parsed as extra query
        # params; _render_page html-escapes the value again once parse_qs on
        # the other end has decoded it back.
        self.send_response(303)
        self.send_header("Location", f"/?msg={quote(message)}")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/":
            self._send_html(_render_page(query.get("msg", [""])[0]))
        elif parsed.path in ("/start", "/stop", "/update"):
            hc_path = query.get("hc_path", [""])[0]
            # Only ever act on a path that's actually in the registry - the
            # query param alone must not be enough to make this endpoint
            # run `docker compose`/git against an arbitrary local directory.
            registered_paths = {entry.get("hc_path") for entry in project_registry.load()}
            if hc_path not in registered_paths:
                message = "Not a registered project."
            elif parsed.path == "/start":
                message = _start_stack(Path(hc_path))
            elif parsed.path == "/stop":
                message = _stop_stack(Path(hc_path))
            else:
                channel = query.get("channel", ["release"])[0]
                if channel not in update_hc.CHANNELS:
                    message = f"Unknown update channel '{channel}'."
                else:
                    message = update_hc.run_update(Path(hc_path), channel=channel)
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
