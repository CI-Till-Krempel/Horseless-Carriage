#!/usr/bin/env python3
"""
Headless driver for the Horseless Carriage team-performance evaluation
harness (see RELEASE.md "Team performance evaluation").

Drives the Scrum team through a fixed number of sprints against the fixed
product vision in eval/scenario/PRODUCT-VISION.md, targeting a dedicated
branch of a dedicated public eval repo so results are comparable across
Horseless Carriage versions over time. Uses a cheap model tier
(scrum-eval-cheap, see litellm.yaml) and a small hard budget cap.

Deliberate simplification: there is no human in the loop, but
create_release_pr/gh_pr_create still open real PRs (this is the team's
real, unmodified behavior - see DEV_PROMPT/PO_PROMPT). Under GitFlow, this
run gets its own isolated main+develop branch pair (eval/<run-id>/main,
eval/<run-id>/develop - see _prepare_local_clone); story-level feature-branch
PRs are merged by the QA agent's own merge_story_pr call during the sprint
(real behavior, no harness involvement), while this script auto-merges only
the sprint-level (develop->main) PR once each sprint's invocation finishes,
standing in for the "Human Review is mandatory" gate real usage requires for
that one merge. That's a real, deliberate change of the eval's observed
behavior vs. production, and must be called out in the generated report,
not just quietly relied upon.

Usage:
    python3 -m agents.scrum_team.scripts.run_eval --sprints 5 --run-id <id>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from agents.scrum_team.scripts._eval_git_utils import get_github_token, run_git, eval_repo_slug
from agents.scrum_team.helpers import get_env_with_deprecated_fallback

DEFAULT_EVAL_REPO_URL = "git@github.com:CI-Till-Krempel/horseless-carriage-eval-todo-app.git"
EVAL_ROLES = ["ORCHESTRATOR", "PO", "SM", "DEV", "QA", "ARCH", "QUALITY"]
SCENARIO_PATH = Path(__file__).resolve().parents[3] / "eval" / "scenario" / "PRODUCT-VISION.md"


def _litellm_proxy_reachable(proxy_base: str) -> bool:
    """Best-effort check that the LiteLLM proxy is actually up, not just configured.

    See README.md "Budget Management": the USD budget guardrail lives in
    check_cost_budget_callback's step 2 (agents/scrum_team/agent.py) and is
    skipped outright - not just failed closed - when LITELLM_MASTER_KEY /
    LITELLM_PROXY_API_BASE aren't set. A configured-but-unreachable proxy still
    fails closed at call time, but that's cold comfort for a local run: better
    to catch it here, before any spend happens, than after.
    """
    try:
        resp = requests.get(f"{proxy_base}/health/readiness", timeout=5)
        return resp.ok
    except requests.RequestException:
        return False


def _configure_env(args: argparse.Namespace) -> None:
    """Sets every env var the agent stack reads, before it's imported."""
    for role in EVAL_ROLES:
        os.environ[f"SCRUM_{role}_MODEL"] = args.model
    os.environ["GITHUB_REPO_URL"] = args.eval_repo_url
    # GitFlow: args.branch is this run's isolated "main" (sprint PRs merge
    # into it); args.develop_branch is its "develop" (feature-branch PRs
    # merge into it) - see _develop_branch_name/_default_push_branch in
    # tools/base.py, and _prepare_local_clone below which bootstraps both.
    os.environ["GITHUB_REPO_BRANCH"] = args.branch
    os.environ["GITHUB_DEVELOP_BRANCH"] = args.develop_branch
    os.environ["STATE_REPO_PATH"] = str(args.local_path)
    os.environ["INTERNAL_STATE_REPO_PATH"] = str(args.local_path)
    os.environ["SESSION_ID"] = f"eval-{args.run_id}"
    os.environ["SPRINT_TOKEN_BUDGET"] = str(args.token_budget)
    # TOTAL_USD_BUDGET is the canonical name (GH issue #81) for the
    # whole-engagement USD ceiling check_cost_budget_callback enforces -
    # this harness fully controls the env before the agent stack is
    # imported, so no deprecated-fallback is needed here (unlike
    # init_scrum_state/check_cost_budget_callback, which also honor the old
    # SPRINT_USD_BUDGET name for a real user's existing .env).
    os.environ["TOTAL_USD_BUDGET"] = str(args.usd_budget)
    # Read by agents/scrum_team/tools/base.py's _with_eval_branch_prefix /
    # _with_eval_title_prefix to tag every branch/PR this run creates with
    # its run id - never set in real/production usage.
    os.environ["EVAL_RUN_ID"] = args.run_id
    # See docs/INTERACTION-LEVELS.md - this run has no human in the loop at
    # all (see this module's docstring), so neither advance_story_stage's
    # "Implemented" gate nor create_release_pr's gate should require any
    # record_human_approval call; _kickoff_message/_sprint_message telling
    # the model to "treat the sprint as pre-approved" is now reinforced by a
    # mechanical guarantee, not just prompt text the model has to trust.
    os.environ["INTERACTION_LEVEL"] = "EVAL"
    # Bootstrap only: the Orchestrator's very first call runs before any
    # virtual key exists (see check_cost_budget_callback's Orchestrator
    # exemption in agents/scrum_team/agent.py). Every other agent stays
    # hard-blocked until it has its own budget-attached virtual key, so
    # this fallback can't turn into unmonitored spend for the team as a
    # whole - only for that one bootstrap call.
    if not os.environ.get("LITELLM_PROXY_API_KEY"):
        os.environ["LITELLM_PROXY_API_KEY"] = os.environ.get("LITELLM_MASTER_KEY", "")


def _prepare_local_clone(repo_url: str, main_branch: str, develop_branch: str, local_path: Path, github_token: str) -> None:
    """
    Clones the eval repo fresh (via `gh repo clone`, reusing the already
    gh-authenticated container - see entrypoint.sh - rather than assuming
    SSH keys are configured for a raw `git clone`), then bootstraps this
    run's isolated main+develop branch pair (GitFlow - mirrors what
    configure_github_repo/_ensure_remote_branch_exists do for real/non-eval
    usage), so the team's very first tool call already operates on isolated
    branches rather than the eval repo's real default branch.
    """
    if local_path.exists() and any(local_path.iterdir()):
        raise RuntimeError(f"local_path already exists and is non-empty, refusing to reuse it: {local_path}")
    if local_path.exists():
        local_path.rmdir()  # gh repo clone requires the target dir to not exist yet
    local_path.parent.mkdir(parents=True, exist_ok=True)
    clone = subprocess.run(
        ["gh", "repo", "clone", eval_repo_slug(repo_url), str(local_path)],
        capture_output=True, text=True,
    )
    if clone.returncode != 0:
        raise RuntimeError(f"Failed to clone eval repo: {clone.stderr}")

    # main first (from whatever the clone's current HEAD is) ...
    checkout_main = run_git(["checkout", "-b", main_branch], cwd=local_path, github_token=github_token)
    if checkout_main.get("status") != "ok":
        raise RuntimeError(f"Failed to create eval main branch {main_branch}: {checkout_main.get('stderr') or checkout_main.get('message')}")
    # Push it immediately: gh_pr_create/create_release_pr default `base` to
    # this branch (see _default_push_branch), and `gh pr create --base
    # <branch>` fails outright if <branch> doesn't exist on the remote yet.
    # Without this push, every PR the team tries to open all run fails
    # silently (create_release_pr doesn't surface the failure - see its own
    # docstring) and the run finishes having created feature branches but
    # zero PRs, as happened in 0.1.0-run4.
    push_main = run_git(["push", "-u", "origin", main_branch], cwd=local_path, github_token=github_token)
    if push_main.get("status") != "ok":
        raise RuntimeError(f"Failed to push eval main branch {main_branch} to origin: {push_main.get('stderr') or push_main.get('message')}")

    # ... then develop, branched from that same commit.
    checkout_develop = run_git(["checkout", "-b", develop_branch], cwd=local_path, github_token=github_token)
    if checkout_develop.get("status") != "ok":
        raise RuntimeError(f"Failed to create eval develop branch {develop_branch}: {checkout_develop.get('stderr') or checkout_develop.get('message')}")
    push_develop = run_git(["push", "-u", "origin", develop_branch], cwd=local_path, github_token=github_token)
    if push_develop.get("status") != "ok":
        raise RuntimeError(f"Failed to push eval develop branch {develop_branch} to origin: {push_develop.get('stderr') or push_develop.get('message')}")


def _sync_local_clone_to_branch(branch: str, local_path: Path, github_token: str) -> None:
    """
    Between sprints: bring the local clone back to develop's latest merged
    state (where ongoing sprint work happens - story-level feature-branch
    PRs merge into develop via QA's own merge_story_pr call during the
    sprint), so the next sprint's "check existing repo content" and new
    feature branches build on top of everything merged so far, not a stale
    or diverged local HEAD.
    """
    run_git(["fetch", "origin", branch], cwd=local_path, github_token=github_token)
    run_git(["checkout", branch], cwd=local_path, github_token=github_token)
    run_git(["pull", "--ff-only", "origin", branch], cwd=local_path, github_token=github_token)


# PR comment body cap: GitHub rejects bodies over 65536 chars outright: stay
# well under that so a very chatty sprint still posts (truncated) rather
# than failing to comment at all.
MAX_TRANSCRIPT_CHARS = 60000


def _format_sprint_transcript(sprint_result: dict, truncate: bool = True) -> str:
    """
    Renders sprint_result["events"] (author/text/tool_calls/tool_responses
    per ADK event - see _run_one_sprint) as the raw, otherwise-nowhere-else
    -surfaced record of what the agent team actually did this sprint.

    truncate=True (used for the PR comment posted by _post_sprint_transcript)
    caps this at MAX_TRANSCRIPT_CHARS - a real sprint's conversation can run
    well past that, silently dropping most of "the actual conversation" from
    the comment. truncate=False (used for the full-run transcript file - see
    _format_full_transcript/write_full_transcript) renders everything, since
    that file isn't posted anywhere size-limited.
    """
    n = sprint_result.get("sprint_number")
    lines = [
        f"## Raw agent activity log - sprint {n}",
        "",
        f"Stop reason: `{sprint_result.get('stop_reason')}` - {sprint_result.get('event_count')} events.",
        "",
    ]
    for i, event in enumerate(sprint_result.get("events") or [], start=1):
        lines.append(f"### Event {i} - {event.get('author')}")
        for call in event.get("tool_calls") or []:
            # Backward-compatible with the old name-only shape, in case
            # anything still hands one of those in (e.g. a test fixture).
            name = call.get("name") if isinstance(call, dict) else call
            args = call.get("args") if isinstance(call, dict) else None
            lines.append(f"- Tool call: `{name}`")
            if args:
                lines += ["  ```json", json.dumps(args, indent=2, default=str), "  ```"]
        for resp in event.get("tool_responses") or []:
            name = resp.get("name") if isinstance(resp, dict) else resp
            response = resp.get("response") if isinstance(resp, dict) else None
            lines.append(f"- Tool response: `{name}`")
            if response:
                lines += ["  ```json", json.dumps(response, indent=2, default=str), "  ```"]
        if event.get("text"):
            lines += ["", "```", event["text"], "```"]
        lines.append("")

    text = "\n".join(lines)
    if truncate and len(text) > MAX_TRANSCRIPT_CHARS:
        text = (
            text[:MAX_TRANSCRIPT_CHARS]
            + f"\n\n...[truncated - {sprint_result.get('event_count')} events total; "
            "see this run's `transcript.md` CI artifact for the full, untruncated log]..."
        )
    return text


def _format_full_transcript(manifest: dict) -> str:
    """
    Untruncated transcript for every sprint in the run, meant for the CI
    artifact (see eval.yml's "Upload report artifact" step) rather than any
    space-limited destination like a PR comment.
    """
    header = [f"# Full agent activity log - run {manifest.get('run_id')}", ""]
    sprints = "\n\n".join(_format_sprint_transcript(s, truncate=False) for s in manifest.get("sprints", []))
    return "\n".join(header) + "\n\n" + sprints


def _ci_run_url() -> str | None:
    """Link back to this CI job's own run, so a PR comment in the eval repo can point at the full-transcript artifact living on the Horseless Carriage side. None outside GitHub Actions (see GITHUB_ACTIONS check in main())."""
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not (server and repo and run_id):
        return None
    return f"{server}/{repo}/actions/runs/{run_id}"


def _post_sprint_transcript(local_path: Path, pr_number: int, sprint_result: dict) -> dict:
    body = _format_sprint_transcript(sprint_result)
    ci_url = _ci_run_url()
    if ci_url:
        body += f"\n\nFull, untruncated transcript for the whole run: see `transcript.md` in the uploaded artifacts on [this CI run]({ci_url})."
    comment = subprocess.run(
        ["gh", "pr", "comment", str(pr_number), "--body-file", "-"],
        cwd=str(local_path), input=body, capture_output=True, text=True,
    )
    return {"posted": comment.returncode == 0, "message": (comment.stdout + comment.stderr).strip()}


def _merge_open_prs(local_path: Path, base_branch: str, sprint_result: dict | None = None) -> list:
    """
    Auto-merges every open PR targeting base_branch in the eval repo -
    the eval harness's stand-in for human review (see module docstring).
    If sprint_result is given, first posts that sprint's full raw activity
    log as a PR comment (see _format_sprint_transcript) - the PR body
    itself is whatever the agent wrote, so this is the only place the
    underlying prompt/tool-call trail becomes visible to a human reviewer.
    Returns a list of {number, merged, message} for the report.
    """
    results = []
    list_res = subprocess.run(
        ["gh", "pr", "list", "--base", base_branch, "--state", "open", "--json", "number"],
        cwd=str(local_path), capture_output=True, text=True,
    )
    if list_res.returncode != 0:
        return [{"error": f"gh pr list failed: {list_res.stderr.strip()}"}]
    try:
        prs = json.loads(list_res.stdout or "[]")
    except json.JSONDecodeError:
        prs = []
    for pr in prs:
        number = pr["number"]
        transcript_result = _post_sprint_transcript(local_path, number, sprint_result) if sprint_result else None
        merge_res = subprocess.run(
            ["gh", "pr", "merge", str(number), "--merge", "--admin"],
            cwd=str(local_path), capture_output=True, text=True,
        )
        results.append({
            "number": number,
            "merged": merge_res.returncode == 0,
            "message": (merge_res.stdout + merge_res.stderr).strip(),
            "transcript_posted": transcript_result,
        })
    return results


_SPECIALIST_AGENT_NAMES = ["ProductOwner", "ScrumMaster", "DevTeam", "QA", "Architect", "QualityGuardian"]


def _kickoff_message(scenario_text: str) -> str:
    key_calls = "\n".join(f"- create_litellm_virtual_key(agent_name=\"{name}\", ...)" for name in _SPECIALIST_AGENT_NAMES)
    return (
        "This is sprint 1 of a fixed 5-sprint evaluation run. You are already fully "
        "configured (repo, branch, GitHub auth, and budgets are all pre-set via "
        "environment variables) - do not ask me any setup questions.\n\n"
        "Before delegating any work to any specialist agent, you MUST first call "
        "init_scrum_state, then call create_litellm_virtual_key exactly these "
        f"{len(_SPECIALIST_AGENT_NAMES)} times, using these exact agent_name values "
        "(these are the literal internal agent names, not display names - using any "
        "other spelling, e.g. with spaces, will not match and that agent will be "
        "blocked from running):\n"
        f"{key_calls}\n\n"
        "Every specialist agent is hard-blocked in code from making any LLM call at "
        "all until a virtual key exists under its exact agent_name - delegating to "
        "one before its key exists wastes the delegation entirely, it does not queue "
        "or retry. Do this once, up front, then proceed.\n\n"
        "The product vision for this entire evaluation, verbatim, is:\n\n"
        f"{scenario_text}\n\n"
        "Treat the sprint goal and backlog for this sprint as pre-approved by me (the "
        "human) - do not wait for further approval messages, proceed directly through "
        "planning, development, PR creation, CI verification, sprint review "
        "(create_sprint_report), and the release PR (create_release_pr) for this "
        "sprint's increment. Do not ask me anything else during this sprint - make "
        "reasonable decisions yourself and note them as assumptions in the sprint report."
    )


def _sprint_message(sprint_number: int, total_sprints: int) -> str:
    return (
        f"This is sprint {sprint_number} of {total_sprints}. Continue the product vision "
        "from before - check the existing repo content (specs/, code, .hc/state.json) "
        "before planning, so you build on what's already there rather than repeating or "
        "contradicting it. As before: the sprint goal and backlog are pre-approved, proceed "
        "directly through planning, development, PR creation, CI verification, sprint review "
        "(create_sprint_report), and the release PR (create_release_pr) without waiting for "
        "further approval. Do not ask me anything else during this sprint."
    )


_CONTINUE_NUDGE = (
    "Continue - this sprint is not yet complete (no sprint report has been produced yet). "
    "You just described a next action without actually taking it - actually call the tool "
    "for that next action now (e.g. transfer_to_agent, or whichever tool moves the sprint "
    "forward), don't just restate the plan."
)


async def _run_one_sprint(runner, session_service, app_name: str, user_id: str, session_id: str, message_text: str, max_events: int, deadline: float, max_nudges: int = 4) -> dict:
    """
    Sends message_text, then - if the model stops with plain text and no
    NEW sprint report yet, rather than an actual tool call moving the
    sprint forward - sends a bounded number of "continue" nudges. A
    cheap/fast model sometimes announces its next action ("Next actions:
    transfer to X") without a tool call actually doing it in the same
    turn; a single scripted message per sprint isn't always enough to get
    through a full plan -> build -> review -> release cycle unattended.

    ScrumState.sprint_report is never cleared between sprints by the
    product code itself (it's just whatever the last create_sprint_report
    call produced) - so "is a report present" is only a valid completion
    signal for *this* sprint if it's explicitly reset first. Passed as a
    state_delta on the very first message of this sprint, rather than
    mutating session.state directly (ADK session state is meant to be
    updated via events/state_delta, not poked from outside the
    conversation).

    `deadline` (time.monotonic() seconds) is a wall-clock safety net,
    independent of the token/USD budget guardrails - if those don't
    actually stop things for whatever reason (a bug, an unexpected model
    behavior), this still bails out and lets the caller write out
    whatever's been gathered so far, rather than running indefinitely
    until something else (e.g. the CI job's hard timeout) kills the
    process with no report at all.
    """
    from google.genai import types

    events = []
    final_text = None
    sprint_report = None
    stop_reason = "max_nudges_exhausted"
    # Fetched unconditionally so it's always defined below, even if the
    # deadline is already past before the first attempt runs (e.g. a
    # prior sprint used up the whole time budget).
    session = await session_service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)

    for attempt in range(max_nudges + 1):
        if time.monotonic() >= deadline:
            stop_reason = "max_duration_exceeded"
            break

        text = message_text if attempt == 0 else _CONTINUE_NUDGE
        message = types.Content(role="user", parts=[types.Part(text=text)])
        # token_usage resets here too, at the first attempt of THIS sprint -
        # SPRINT_TOKEN_BUDGET/EVAL_SPRINT_TOKEN_BUDGET is a per-sprint
        # allowance, not cumulative for the whole run (see
        # check_cost_budget_callback in agent.py); without this, one
        # expensive sprint silently starves every later sprint of further
        # LLM calls. Harness-side equivalent of the reset_sprint_budget
        # tool Scrum Master calls in interactive/real usage.
        state_delta = (
            {
                "sprint_report": "",
                "token_usage": {"total": 0, "agents": {}},
                "budget_exhaustion_synced": False,
                # GH issue #124: sprint_report_kpis is never cleared by the
                # product code itself either (same as sprint_report above) -
                # without this reset, a sprint where QualityGuardian doesn't
                # get to run would silently inherit the previous sprint's
                # KPI values instead of correctly having none.
                "sprint_report_kpis": {},
            }
            if attempt == 0 else None
        )

        try:
            async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=message, state_delta=state_delta):
                record = {"author": event.author, "text": None, "tool_calls": [], "tool_responses": []}
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if getattr(part, "text", None):
                            record["text"] = part.text
                            final_text = part.text
                        # Capture args/response, not just the tool name - the
                        # actual substance of what an agent wrote (a PR comment
                        # body, a sprint report, source code passed to
                        # write_file) lives there. A cheap model often makes a
                        # tool call with little or no accompanying free text, so
                        # name-only logging looked like "just tool calls, no
                        # conversation" even though the real content was one
                        # field over the whole time.
                        if getattr(part, "function_call", None):
                            fc = part.function_call
                            record["tool_calls"].append({"name": fc.name, "args": dict(fc.args) if fc.args else {}})
                        if getattr(part, "function_response", None):
                            fr = part.function_response
                            response = fr.response if isinstance(fr.response, dict) else {"value": fr.response}
                            record["tool_responses"].append({"name": fr.name, "response": response})
                events.append(record)
                if len(events) >= max_events or time.monotonic() >= deadline:
                    break
        except ValueError as e:
            # A cheap/fast model occasionally hallucinates a transfer_to_agent
            # call targeting its own currently-active agent (e.g. DevTeam ->
            # DevTeam). ADK's own transfer-resolution (_transfer_utils.py)
            # validates this and raises before any of our tool-error-callback
            # machinery ever runs (on_tool_error_callback in agent.py only
            # covers dispatch-time "tool not found" errors - a different ADK
            # code path that never sees this one). Re-raise anything else so a
            # real bug doesn't get silently swallowed here.
            if "cannot transfer to itself" not in str(e):
                raise
            print(f"WARNING: ADK rejected a self-transfer mid-turn ({e}) - ending this sprint's turn early.", file=sys.stderr)
            stop_reason = "adk_self_transfer_error"
            session = await session_service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
            sprint_report = session.state.get("sprint_report")
            break

        session = await session_service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
        sprint_report = session.state.get("sprint_report")
        if sprint_report:
            stop_reason = "sprint_report_produced"
            break
        if len(events) >= max_events:
            stop_reason = "max_events_reached"
            break
        if time.monotonic() >= deadline:
            stop_reason = "max_duration_exceeded"
            break

    return {
        "final_text": final_text,
        "event_count": len(events),
        "events": events,
        "token_usage": session.state.get("token_usage"),
        "sprint_report": sprint_report,
        "sprint_backlog": session.state.get("sprint_backlog"),
        "product_backlog": session.state.get("product_backlog"),
        # GH issue #124: QualityGuardian's calculate_kpis/update_sprint_report
        # (see its prompt's "YOU DO") stores its findings here - captured so
        # run_eval_analysis.py can plot Say-Do Ratio/Quality/Test Coverage as
        # a time series across sprints. Only present if QualityGuardian
        # actually got to run this sprint (cheap-model/budget-constrained
        # sprints sometimes don't) - None otherwise, not a fabricated value.
        "sprint_report_kpis": session.state.get("sprint_report_kpis"),
        "stop_reason": stop_reason,
        # Set by _notify_critical_halt (agent.py) whenever
        # check_cost_budget_callback halts this sprint on a token/USD
        # guardrail - a real run kept silently starting the next sprint
        # fresh after this instead of stopping (its own token/state resets
        # made the halted sprint look like it never happened). The caller
        # uses this to actually stop the whole run.
        "critical_halt": bool(session.state.get("critical_halt_notified")),
    }


async def _main_async(args: argparse.Namespace) -> dict:
    from google.adk.runners import InMemoryRunner
    from google.adk.sessions import InMemorySessionService
    from agents.scrum_team.agent import root_agent

    app_name = "hc_eval"
    user_id = "eval"
    session_service = InMemorySessionService()
    runner = InMemoryRunner(agent=root_agent, app_name=app_name)
    runner.session_service = session_service
    session = await session_service.create_session(app_name=app_name, user_id=user_id)

    scenario_text = SCENARIO_PATH.read_text(encoding="utf-8")
    # Wall-clock safety net (see _run_one_sprint's docstring): independent
    # of the token/USD budget guardrails, so a bug or unexpected model
    # behavior in *those* can't turn into an unbounded run. Deliberately
    # NOT reset per-sprint - it's a ceiling on the whole run, matching
    # what the CI job's own hard timeout is protecting against.
    deadline = time.monotonic() + args.max_duration_minutes * 60
    manifest = {
        "run_id": args.run_id,
        "branch": args.branch,
        "develop_branch": args.develop_branch,
        "eval_repo_url": args.eval_repo_url,
        "model": args.model,
        # GH issue #125: which Horseless Carriage commit produced this run,
        # so results are comparable across HC versions/fixes over time, not
        # just across eval-repo branches. `.git` is deliberately excluded
        # from the agent image's build context (see .dockerignore, GH issue
        # #123), so this can't be computed with a `git` call from inside
        # the container - the caller (eval.yml, or a local run) must
        # capture it on the host and pass it through as HC_COMMIT_SHA.
        "hc_commit": os.environ.get("HC_COMMIT_SHA", "unknown"),
        "sprints_requested": args.sprints,
        "max_duration_minutes": args.max_duration_minutes,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "sprints": [],
        "pr_merges": [],
        "stopped_early": False,
    }

    for sprint_number in range(1, args.sprints + 1):
        if time.monotonic() >= deadline:
            print(f"--- max duration ({args.max_duration_minutes}m) reached before sprint {sprint_number}/{args.sprints} - stopping ---", file=sys.stderr)
            manifest["stopped_early"] = True
            manifest["stop_reason"] = "max_duration_exceeded"
            break

        message_text = _kickoff_message(scenario_text) if sprint_number == 1 else _sprint_message(sprint_number, args.sprints)
        print(f"--- sprint {sprint_number}/{args.sprints}: sending scripted message ---", file=sys.stderr)
        try:
            sprint_result = await _run_one_sprint(
                runner, session_service, app_name, user_id, session.id, message_text, args.max_events_per_sprint, deadline,
            )
            sprint_result["sprint_number"] = sprint_number
            manifest["sprints"].append(sprint_result)
            if sprint_result["stop_reason"] == "max_duration_exceeded":
                manifest["stopped_early"] = True
                manifest["stop_reason"] = "max_duration_exceeded"

            # Only the sprint-level (develop->main) PR is auto-merged here -
            # base_branch=args.branch (this run's main) narrows _merge_open_prs
            # so story-level feature->develop PRs are left alone; those are
            # merged by QA's own merge_story_pr call during the sprint instead.
            merges = _merge_open_prs(args.local_path, args.branch, sprint_result=sprint_result)
            manifest["pr_merges"].extend([{**m, "after_sprint": sprint_number} for m in merges])
            _sync_local_clone_to_branch(args.develop_branch, args.local_path, args.github_token)
        except Exception as e:
            # Whatever crashed (a real run hit an uncaught litellm.RateLimitError
            # here, see agent.py's _patched_adk_acompletion for the fix to that
            # specific case) - don't lose every sprint's data gathered so far.
            # main() writes out whatever manifest _main_async returns, crashed
            # or not, so this still leaves a usable partial report/transcript
            # on disk instead of nothing at all.
            print(f"--- sprint {sprint_number}/{args.sprints} crashed: {type(e).__name__}: {e} ---", file=sys.stderr)
            manifest["stopped_early"] = True
            manifest["stop_reason"] = "crashed"
            manifest["crash_sprint"] = sprint_number
            manifest["crash_error"] = f"{type(e).__name__}: {e}"
            break

        if sprint_result["stop_reason"] == "max_duration_exceeded":
            break

        if sprint_result["critical_halt"]:
            # Previously this fell through to the next sprint's fresh
            # token/state reset as if the halt never happened (a real run
            # hit this in sprint 1, then bounced into an unrelated
            # transfer-loop crash in sprint 2) - a token/USD guardrail
            # tripping is a genuine "stop the run" signal, not a per-sprint
            # speed bump to silently absorb.
            print(
                f"--- sprint {sprint_number}/{args.sprints} hit a critical budget halt - stopping run ---",
                file=sys.stderr,
            )
            manifest["stopped_early"] = True
            manifest["stop_reason"] = "budget_critical_halt"
            break

    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sprints", type=int, default=5)
    parser.add_argument("--run-id", required=True, help="Unique identifier for this run, e.g. <hc-version>-<gh-run-number>")
    parser.add_argument("--eval-repo-url", default=DEFAULT_EVAL_REPO_URL)
    parser.add_argument("--branch", default=None, help="This run's isolated 'main' - defaults to eval/<run-id>/main")
    parser.add_argument("--develop-branch", default=None, help="This run's isolated 'develop' - defaults to eval/<run-id>/develop")
    parser.add_argument("--local-path", default=None, help="Defaults to a fresh temp dir")
    parser.add_argument("--model", default="scrum-eval-cheap")
    # token_usage now resets at the start of every sprint (see
    # _run_one_sprint's state_delta), so this is ONE sprint's allowance, not
    # the whole run's - unlike --usd-budget below, do NOT scale this by
    # --sprints. Defaulted to None here and resolved below from
    # EVAL_SPRINT_TOKEN_BUDGET once args.sprints is known.
    parser.add_argument("--token-budget", type=int, default=None, help="Defaults to EVAL_SPRINT_TOKEN_BUDGET (see .env) - a per-sprint value, not scaled by --sprints")
    # scrum-eval-cheap is cheap enough that token budget binds first in
    # practice; this stays as the secondary $-denominated safety net.
    # Also resolved below from EVAL_USD_BUDGET_PER_SPRINT, scaled by --sprints.
    parser.add_argument("--usd-budget", type=float, default=None, help="Defaults to --sprints * EVAL_USD_BUDGET_PER_SPRINT (see .env)")
    parser.add_argument("--max-events-per-sprint", type=int, default=300, help="Safety cap on ADK events per sprint invocation (excluding continue-nudges)")
    # Wall-clock ceiling for the whole run (all sprints combined), independent
    # of token/USD budget - see _main_async. Default leaves headroom under
    # eval.yml's 60-minute job timeout for service startup, the analysis
    # step, artifact upload, and teardown.
    parser.add_argument("--max-duration-minutes", type=int, default=40)
    parser.add_argument("--report-path", default=None, help="Where to write the raw run manifest JSON")
    parser.add_argument("--transcript-path", default=None, help="Where to write the full, untruncated conversation log (see _format_full_transcript)")
    # Gate for the local-run-without-a-live-proxy footgun below - deliberately
    # not needed in CI, where eval.yml already waits for /health/readiness
    # before this script ever runs.
    parser.add_argument(
        "--dev-mode",
        action="store_true",
        help="Required to proceed with a local run when the LiteLLM proxy isn't reachable "
        "(the USD budget guardrail is enforced by the proxy - see README.md 'Budget "
        "Management' - and silently does not apply without it, only the local token-count "
        "guardrail still runs). Only pass this if you understand and accept that.",
    )
    args = parser.parse_args()

    if args.branch is None:
        args.branch = f"eval/{args.run_id}/main"
    if args.develop_branch is None:
        args.develop_branch = f"eval/{args.run_id}/develop"
    if args.local_path is None:
        import tempfile
        args.local_path = Path(tempfile.mkdtemp(prefix="hc-eval-"))
    else:
        args.local_path = Path(args.local_path)
    if args.report_path is None:
        args.report_path = f"eval-run-{args.run_id.replace('/', '-')}.json"
    if args.transcript_path is None:
        args.transcript_path = f"eval-transcript-{args.run_id.replace('/', '-')}.md"
    # token_usage now resets at the start of every sprint (see
    # _run_one_sprint's state_delta) - SPRINT_TOKEN_BUDGET/
    # EVAL_SPRINT_TOKEN_BUDGET is a per-sprint allowance, so args.token_budget
    # is used as-is, NOT scaled by --sprints (previously it was scaled,
    # compensating for token_usage never resetting - which is also why
    # sprints 2-3 of run 0.1.0-run2 (2026-07-21) did nothing: one expensive
    # sprint permanently starved the rest of that run). The per-sprint value
    # itself lives in .env (EVAL_SPRINT_TOKEN_BUDGET, next to
    # SPRINT_TOKEN_BUDGET) rather than here, so recalibrating doesn't require
    # a code change.
    if args.token_budget is None:
        per_sprint_token_budget = os.environ.get("EVAL_SPRINT_TOKEN_BUDGET")
        if not per_sprint_token_budget:
            parser.error(
                "--token-budget not given and EVAL_SPRINT_TOKEN_BUDGET is not "
                "set - see .env.example's 'Eval Harness Budget Configuration' section."
            )
        args.token_budget = int(per_sprint_token_budget)
    # The USD budget, unlike the token budget above, stays a whole-run
    # cumulative ceiling by design (see BUDGET.md/reset_sprint_budget) -
    # enforced by the LiteLLM proxy's shared scrum-sprint-budget object, not
    # reset per sprint - so this scaling is unchanged. EVAL_USD_BUDGET_PER_SPRINT
    # is the canonical name (GH issue #81 - "per sprint" in the name makes
    # clear this is a *rate* that gets multiplied by --sprints below, not
    # itself the whole-run ceiling); EVAL_SPRINT_USD_BUDGET is still read as
    # a deprecated fallback.
    if args.usd_budget is None:
        per_sprint_usd_budget = get_env_with_deprecated_fallback("EVAL_USD_BUDGET_PER_SPRINT", "EVAL_SPRINT_USD_BUDGET")
        if not per_sprint_usd_budget:
            parser.error(
                "--usd-budget not given and EVAL_USD_BUDGET_PER_SPRINT is not "
                "set - see .env.example's 'Eval Harness Budget Configuration' section."
            )
        args.usd_budget = args.sprints * float(per_sprint_usd_budget)

    # Heads-up for a human running this on their own machine (as opposed to
    # the eval-approval-gated CI job - see RELEASE.md "Team performance
    # evaluation") that this spends real money against a real LLM. eval.yml
    # always brings the proxy up and waits for /health/readiness before
    # invoking this script, so this whole block is a no-op under CI.
    if not os.environ.get("GITHUB_ACTIONS"):
        proxy_base = os.environ.get("LITELLM_PROXY_API_BASE")
        master_key = os.environ.get("LITELLM_MASTER_KEY")
        proxy_ok = bool(proxy_base and master_key and _litellm_proxy_reachable(proxy_base))
        if not proxy_ok:
            bar = "!" * 78
            print(
                f"\n{bar}\n"
                "!! LITELLM PROXY NOT REACHABLE\n"
                "!!\n"
                "!! The USD budget guardrail (README.md \"Budget Management\") is enforced\n"
                "!! by the LiteLLM proxy and will NOT apply without it - only the local\n"
                f"!! token-count guardrail (up to {args.token_budget:,} tokens) still runs.\n"
                f"!! A misbehaving run could spend well past the intended ${args.usd_budget:.2f}\n"
                "!! cap with no guardrail catching it.\n"
                f"{bar}\n",
                file=sys.stderr,
            )
            if not args.dev_mode:
                parser.error(
                    "Refusing to run without a reachable LiteLLM proxy (see warning above). "
                    "Start it with `docker compose up -d db litellm`, or pass --dev-mode if "
                    "you understand the USD budget guardrail won't be enforced."
                )
        print(
            f"\n⚠️  About to run {args.sprints} sprint(s) against real LLM "
            f"traffic via LiteLLM, budgeted up to {args.token_budget:,} tokens "
            f"/ ${args.usd_budget:.2f} total for this run. This is real spend, "
            "not a dry run.\n",
            file=sys.stderr,
        )

    args.github_token = get_github_token()

    _configure_env(args)
    _prepare_local_clone(args.eval_repo_url, args.branch, args.develop_branch, args.local_path, args.github_token)

    manifest = asyncio.run(_main_async(args))

    Path(args.report_path).write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print(f"Run manifest written to {args.report_path}")

    Path(args.transcript_path).write_text(_format_full_transcript(manifest), encoding="utf-8")
    print(f"Full transcript written to {args.transcript_path}")


if __name__ == "__main__":
    main()
