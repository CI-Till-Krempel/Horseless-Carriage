"""
Shared git/gh helpers for the team-performance evaluation harness
(run_eval.py, run_eval_analysis.py). Not a public tool module - internal
to the eval scripts only.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


class EvalToolContext:
    """
    Minimal tool_context stand-in so eval-script git operations (outside
    any agent conversation) get the same AUTHORIZATION-header injection
    _run() gives every agent tool call - see agents/scrum_team/tools/base.py.
    Without this, a plain `git push`/`git pull` here has no credentials at
    all, unlike `gh` CLI calls (which have their own auth) or the agents'
    own git_push/gh_pr_create tool calls (which go through _run with a
    real tool_context).
    """
    def __init__(self, github_token: str):
        self.state = {"github_token": github_token}
        self.agent_name = "EvalHarness"


def get_github_token() -> str:
    """
    `gh auth token` (not a GITHUB_TOKEN/GH_TOKEN env var) is the source of
    truth: entrypoint.sh's GitHub App auth (auth_github.py) generates a
    fresh installation token and feeds it straight into `gh auth login`,
    without ever exporting it as an env var. `gh auth token` is gh's own
    way to retrieve whatever token it has configured, working the same
    way regardless of whether App or personal-token auth was used.
    """
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    token = result.stdout.strip() if result.returncode == 0 else ""
    if not token:
        raise SystemExit(
            "`gh auth token` returned nothing - the eval harness needs push access "
            "to the eval repo. Run this script through the normal entrypoint (not "
            f"with it overridden) so GitHub auth runs first. stderr: {result.stderr.strip()}"
        )
    return token


def run_git(args: list, cwd: Path, github_token: str) -> dict:
    from agents.scrum_team.tools.base import _run
    return _run(["git", *args], cwd=str(cwd), tool_context=EvalToolContext(github_token))


def eval_repo_slug(repo_url: str) -> str:
    """git@github.com:owner/repo.git or https://github.com/owner/repo(.git) -> owner/repo"""
    slug = repo_url.split("github.com")[-1].lstrip(":/")
    return slug[:-4] if slug.endswith(".git") else slug
