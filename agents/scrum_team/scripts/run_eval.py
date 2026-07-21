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
real, unmodified behavior - see DEV_PROMPT/PO_PROMPT). This script
auto-merges any open PR on the eval branch once its sprint invocation
finishes, standing in for the "Human Review is mandatory" gate real
usage requires. That's a real, deliberate change of the eval's
observed behavior vs. production, and must be called out in the
generated report, not just quietly relied upon.

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
from datetime import datetime, timezone
from pathlib import Path

from agents.scrum_team.scripts._eval_git_utils import get_github_token, run_git, eval_repo_slug

DEFAULT_EVAL_REPO_URL = "git@github.com:CI-Till-Krempel/horseless-carriage-eval-todo-app.git"
EVAL_ROLES = ["ORCHESTRATOR", "PO", "SM", "DEV", "QA", "ARCH", "QUALITY"]
SCENARIO_PATH = Path(__file__).resolve().parents[3] / "eval" / "scenario" / "PRODUCT-VISION.md"


def _configure_env(args: argparse.Namespace) -> None:
    """Sets every env var the agent stack reads, before it's imported."""
    for role in EVAL_ROLES:
        os.environ[f"SCRUM_{role}_MODEL"] = args.model
    os.environ["GITHUB_REPO_URL"] = args.eval_repo_url
    os.environ["GITHUB_REPO_BRANCH"] = args.branch
    os.environ["STATE_REPO_PATH"] = str(args.local_path)
    os.environ["INTERNAL_STATE_REPO_PATH"] = str(args.local_path)
    os.environ["SESSION_ID"] = f"eval-{args.branch.replace('/', '-')}"
    os.environ["SPRINT_TOKEN_BUDGET"] = str(args.token_budget)
    os.environ["SPRINT_USD_BUDGET"] = str(args.usd_budget)
    # Bootstrap only: the Orchestrator's very first call runs before any
    # virtual key exists (see check_cost_budget_callback's Orchestrator
    # exemption in agents/scrum_team/agent.py). Every other agent stays
    # hard-blocked until it has its own budget-attached virtual key, so
    # this fallback can't turn into unmonitored spend for the team as a
    # whole - only for that one bootstrap call.
    if not os.environ.get("LITELLM_PROXY_API_KEY"):
        os.environ["LITELLM_PROXY_API_KEY"] = os.environ.get("LITELLM_MASTER_KEY", "")


def _prepare_local_clone(repo_url: str, branch: str, local_path: Path, github_token: str) -> None:
    """
    Clones the eval repo fresh (via `gh repo clone`, reusing the already
    gh-authenticated container - see entrypoint.sh - rather than assuming
    SSH keys are configured for a raw `git clone`) and checks out a new
    local branch for this run, so the team's very first tool call already
    operates on the isolated eval branch rather than the eval repo's real
    main.
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
    checkout = run_git(["checkout", "-b", branch], cwd=local_path, github_token=github_token)
    if checkout.get("status") != "ok":
        raise RuntimeError(f"Failed to create eval branch {branch}: {checkout.get('stderr') or checkout.get('message')}")


def _sync_local_clone_to_branch(branch: str, local_path: Path, github_token: str) -> None:
    """
    Between sprints: bring the local clone back to the eval branch's
    latest merged state, so the next sprint's "check existing repo
    content" and new feature branches build on top of everything merged
    so far, not a stale or diverged local HEAD.
    """
    run_git(["fetch", "origin", branch], cwd=local_path, github_token=github_token)
    run_git(["checkout", branch], cwd=local_path, github_token=github_token)
    run_git(["pull", "--ff-only", "origin", branch], cwd=local_path, github_token=github_token)


def _merge_open_prs(local_path: Path, base_branch: str) -> list:
    """
    Auto-merges every open PR targeting base_branch in the eval repo -
    the eval harness's stand-in for human review (see module docstring).
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
        merge_res = subprocess.run(
            ["gh", "pr", "merge", str(number), "--merge", "--admin"],
            cwd=str(local_path), capture_output=True, text=True,
        )
        results.append({
            "number": number,
            "merged": merge_res.returncode == 0,
            "message": (merge_res.stdout + merge_res.stderr).strip(),
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


async def _run_one_sprint(runner, session_service, app_name: str, user_id: str, session_id: str, message_text: str, max_events: int, max_nudges: int = 4) -> dict:
    """
    Sends message_text, then - if the model stops with plain text and no
    sprint report yet, rather than an actual tool call moving the sprint
    forward - sends a bounded number of "continue" nudges. A cheap/fast
    model sometimes announces its next action ("Next actions: transfer to
    X") without a tool call actually doing it in the same turn; a single
    scripted message per sprint isn't always enough to get through a full
    plan -> build -> review -> release cycle unattended.
    """
    from google.genai import types

    events = []
    final_text = None
    sprint_report = None

    for attempt in range(max_nudges + 1):
        text = message_text if attempt == 0 else _CONTINUE_NUDGE
        message = types.Content(role="user", parts=[types.Part(text=text)])

        async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=message):
            record = {"author": event.author, "text": None, "tool_calls": [], "tool_responses": []}
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if getattr(part, "text", None):
                        record["text"] = part.text
                        final_text = part.text
                    if getattr(part, "function_call", None):
                        record["tool_calls"].append(part.function_call.name)
                    if getattr(part, "function_response", None):
                        record["tool_responses"].append(part.function_response.name)
            events.append(record)
            if len(events) >= max_events:
                break

        session = await session_service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
        sprint_report = session.state.get("sprint_report")
        if sprint_report or len(events) >= max_events:
            break

    return {
        "final_text": final_text,
        "event_count": len(events),
        "events": events,
        "token_usage": session.state.get("token_usage"),
        "sprint_report": sprint_report,
        "sprint_backlog": session.state.get("sprint_backlog"),
        "product_backlog": session.state.get("product_backlog"),
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
    manifest = {
        "run_id": args.run_id,
        "branch": args.branch,
        "eval_repo_url": args.eval_repo_url,
        "model": args.model,
        "sprints_requested": args.sprints,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "sprints": [],
        "pr_merges": [],
    }

    for sprint_number in range(1, args.sprints + 1):
        message_text = _kickoff_message(scenario_text) if sprint_number == 1 else _sprint_message(sprint_number, args.sprints)
        print(f"--- sprint {sprint_number}/{args.sprints}: sending scripted message ---", file=sys.stderr)
        sprint_result = await _run_one_sprint(
            runner, session_service, app_name, user_id, session.id, message_text, args.max_events_per_sprint,
        )
        sprint_result["sprint_number"] = sprint_number
        manifest["sprints"].append(sprint_result)

        merges = _merge_open_prs(args.local_path, args.branch)
        manifest["pr_merges"].extend([{**m, "after_sprint": sprint_number} for m in merges])
        _sync_local_clone_to_branch(args.branch, args.local_path, args.github_token)

    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sprints", type=int, default=5)
    parser.add_argument("--run-id", required=True, help="Unique identifier for this run, e.g. <hc-version>-<gh-run-number>")
    parser.add_argument("--eval-repo-url", default=DEFAULT_EVAL_REPO_URL)
    parser.add_argument("--branch", default=None, help="Defaults to eval/<run-id>")
    parser.add_argument("--local-path", default=None, help="Defaults to a fresh temp dir")
    parser.add_argument("--model", default="scrum-eval-cheap")
    # token_usage is cumulative for the whole session (never resets between
    # sprints - see check_cost_budget_callback), so this must cover all 5
    # sprints combined, not one. Calibrated against a real sprint 1 run:
    # ~260k tokens for planning + a full implementation-and-PR cycle.
    parser.add_argument("--token-budget", type=int, default=2000000)
    # scrum-eval-cheap is cheap enough that token budget binds first in
    # practice; this stays as the secondary $-denominated safety net.
    parser.add_argument("--usd-budget", type=float, default=5.0)
    parser.add_argument("--max-events-per-sprint", type=int, default=300, help="Safety cap on ADK events per sprint invocation (excluding continue-nudges)")
    parser.add_argument("--report-path", default=None, help="Where to write the raw run manifest JSON")
    args = parser.parse_args()

    if args.branch is None:
        args.branch = f"eval/{args.run_id}"
    if args.local_path is None:
        import tempfile
        args.local_path = Path(tempfile.mkdtemp(prefix="hc-eval-"))
    else:
        args.local_path = Path(args.local_path)
    if args.report_path is None:
        args.report_path = f"eval-run-{args.run_id.replace('/', '-')}.json"

    args.github_token = get_github_token()

    _configure_env(args)
    _prepare_local_clone(args.eval_repo_url, args.branch, args.local_path, args.github_token)

    manifest = asyncio.run(_main_async(args))

    Path(args.report_path).write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print(f"Run manifest written to {args.report_path}")


if __name__ == "__main__":
    main()
