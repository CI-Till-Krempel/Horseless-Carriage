# agents/scrum_team/tools/github.py
from __future__ import annotations
import os
import json
from typing import Any, Dict
from .base import _configured_repo_root, _run, _default_push_branch, _with_eval_branch_prefix, _with_eval_title_prefix

def configure_github_repo(repo_url: str, local_path: str = "", default_branch: str | None = None, tool_context=None) -> Dict[str, Any]:
    """
    Configure the GitHub repository used for persistence and tooling.
    - repo_url: SSH or HTTPS URL
    - local_path: optional existing checkout or desired clone path. If empty, will use the path from the STATE_REPO_PATH environment variable.
    - default_branch: branch used for pushes/releases by default. Defaults
      to the configured default (see _default_push_branch) rather than a
      hardcoded "main", so a caller-omitted value can't clobber an
      eval/test run's GITHUB_REPO_BRANCH-configured branch.
    This will clone the repo if local_path does not exist.
    """
    from pathlib import Path
    from .base import _project_root

    default_branch = default_branch or _default_push_branch(tool_context)
    target_dir = _configured_repo_root(tool_context)
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    # If the directory is not a git repo, attempt clone
    if not (target_dir / ".git").exists():
        # Best effort: clone
        try:
            result = _run(["git", "clone", repo_url, str(target_dir)], cwd=str(target_dir.parent), tool_context=tool_context)
            if result.get("status") == "error":
                return {"status": "error", "message": f"Clone failed: {result.get('stderr') or result.get('message')}", "details": result}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # Save config into session.state
    repo_cfg = {
        "url": repo_url,
        "local_path": str(target_dir),
        "default_branch": default_branch,
    }
    tool_context.state["repo"] = repo_cfg
    return {"status": "ok", "repo": repo_cfg}

def configure_github_app(app_id: str, private_key: str, installation_id: str, tool_context=None) -> Dict[str, Any]:
    """
    Configure GitHub App authentication.
    - private_key: content of the .pem file
    - installation_id: ID of the app installation on the repo/org
    """
    import jwt
    import time
    import requests
    from .base import _normalize_private_key
    
    clean_key = _normalize_private_key(private_key)
    
    # 1. Generate JWT
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": str(app_id),
    }
    try:
        encoded_jwt = jwt.encode(payload, clean_key, algorithm="RS256")
    except Exception as e:
        return {"status": "error", "message": f"JWT encoding failed. Check if your GITHUB_APP_PRIVATE_KEY is a valid RSA private key. Error: {e}"}

    # 2. Exchange for Installation Access Token
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {encoded_jwt}",
        "Accept": "application/vnd.github+json",
    }
    try:
        resp = requests.post(url, headers=headers, timeout=10)
        resp.raise_for_status()
        token_data = resp.json()
        token = token_data.get("token")
        
        # Store in state (Session-only, NOT persisted to repo state.json)
        tool_context.state["github_token"] = token
        tool_context.state["github_app"] = {
            "app_id": str(app_id),
            "installation_id": str(installation_id),
        }
        
        return {"status": "ok", "message": "GitHub App authenticated successfully.", "expires_at": token_data.get("expires_at")}
    except Exception as e:
        return {"status": "error", "message": f"Failed to get installation access token: {e}"}

def git_push(branch: str, commit_message: str = "chore: update", add_all: bool = True, allow_protected: bool = False, tool_context=None) -> Dict[str, Any]:
    """
    Stage changes (optionally), commit, and push the current working tree to the given branch.
    Non-interactive; returns command outputs.
    - In an eval run (EVAL_RUN_ID set), branch is auto-tagged with the run id
      (e.g. "eval-<run-id>/<branch>") so branches from different runs sharing
      one eval repo stay distinguishable - see _with_eval_branch_prefix. No-op
      in real usage.
    - allow_protected: escape hatch for the one legitimate direct-push case
      (seed_repository's initial bootstrap commit, before any other branch
      exists to PR from). Defaults False - see ISSUE-0006: DEV_PROMPT's
      "the configured default branch is PROTECTED... cannot push to it
      directly" had no code backing it at all before this.
    """
    branch = _with_eval_branch_prefix(branch)
    protected_branch = _default_push_branch(tool_context)
    if branch == protected_branch and not allow_protected:
        return {
            "status": "error",
            "message": (
                f"Refusing to push directly to '{protected_branch}' - it's the configured "
                "protected default branch (see DEV_PROMPT). Push to a feature branch and open a "
                "Pull Request via gh_pr_create/create_release_pr instead."
            ),
        }
    repo_root = str(_configured_repo_root(tool_context))

    # Ensure branch exists locally
    _ = _run(["git", "checkout", "-B", branch], cwd=repo_root, tool_context=tool_context)

    r1 = None
    if add_all:
        r1 = _run(["git", "add", "-A"], cwd=repo_root, tool_context=tool_context)
        if r1.get("status") == "error":
            return r1
    r2 = _run(["git", "commit", "-m", commit_message], cwd=repo_root, tool_context=tool_context)
    # Allow empty commit to ensure branch gets pushed
    if r2.get("returncode") != 0:
        # Try creating an empty commit when nothing to commit
        if "nothing to commit" in (r2.get("stderr") or "") + (r2.get("stdout") or ""):
            r2 = _run(["git", "commit", "--allow-empty", "-m", commit_message], cwd=repo_root, tool_context=tool_context)
        # If still failing, continue to push in case branch update is desired
    r3 = _run(["git", "push", "-u", "origin", branch], cwd=repo_root, tool_context=tool_context)

    return {"status": "ok" if r3.get("status") == "ok" else "error", "branch": branch, "steps": {"checkout": _, "add": r1, "commit": r2, "push": r3}}

def gh_pr_create(title: str, body: str = "", base: str | None = None, head: str | None = None, draft: bool = False, tool_context=None) -> Dict[str, Any]:
    """
    Create a Pull Request using `gh` CLI. Assumes authentication is set up.
    - base: target branch. Defaults to the configured default branch (see
      _default_push_branch) rather than a hardcoded "main", so an isolated
      eval/test run can target its own branch via GITHUB_REPO_BRANCH.
    - head: source branch (defaults to current if None). In an eval run
      (EVAL_RUN_ID set), auto-tagged with the run id the same way git_push
      tags branches, so a head passed as the plain (unprefixed) branch name
      still resolves - see _with_eval_branch_prefix. No-op in real usage.
    - draft: open PR as draft
    """
    repo_root = str(_configured_repo_root(tool_context))
    base = base or _default_push_branch(tool_context)
    if head:
        head = _with_eval_branch_prefix(head)
    title = _with_eval_title_prefix(title)
    cmd = ["gh", "pr", "create", "--base", base, "--title", title]
    if body:
        cmd += ["--body", body]
    if head:
        cmd += ["--head", head]
    if draft:
        cmd += ["--draft"]
    r = _run(cmd, cwd=repo_root, tool_context=tool_context)
    return r

def gh_pr_status(tool_context=None) -> Dict[str, Any]:
    """
    Check the status of Pull Requests for the current repository and user/app.
    """
    repo_root = str(_configured_repo_root(tool_context))
    r = _run(["gh", "pr", "status"], cwd=repo_root, tool_context=tool_context)
    return r

def gh_pr_checks(pr_id: str | int | None = None, watch: bool = False, interval: int = 10, tool_context=None) -> Dict[str, Any]:
    """
    Check if the PR checks (CI) are passing.
    - pr_id: optional PR number, URL, or branch. If None, uses current branch.
    - watch: if True, waits until all checks finish (blocking).
    - interval: refresh interval in seconds for watch mode.
    Returns status: "ok" (passing or no checks), "pending", or "error" (failing).
    """
    repo_root = str(_configured_repo_root(tool_context))

    # If watch is True, we first wait using `gh pr checks --watch`
    # and then we call it again with --json to get the results.
    if watch:
        watch_cmd = ["gh", "pr", "checks"]
        if pr_id:
            watch_cmd.append(str(pr_id))
        watch_cmd.append("--watch")
        if interval:
            watch_cmd.append("--interval")
            watch_cmd.append(str(interval))
        
        # We don't use --json with --watch as they are incompatible
        _ = _run(watch_cmd, cwd=repo_root, tool_context=tool_context)
        # After watch finishes, we proceed to get JSON results.

    cmd = ["gh", "pr", "checks"]
    if pr_id:
        cmd.append(str(pr_id))
    
    cmd.append("--json")
    cmd.append("state,bucket")

    r = _run(cmd, cwd=repo_root, tool_context=tool_context)
    
    if r.get("status") == "error":
        stderr = r.get("stderr", "")
        stdout = r.get("stdout", "")
        # No checks case
        if "no checks reported" in stderr.lower() or "no checks reported" in stdout.lower():
            return {"status": "ok", "passing": True, "message": "No checks defined.", "details": r}
        
        # Pending case (exit code 8)
        if r.get("returncode") == 8:
            return {"status": "pending", "passing": False, "message": "Checks are pending.", "details": r}
            
        return {"status": "error", "passing": False, "message": "Checks are failing or another error occurred.", "details": r}

    return {"status": "ok", "passing": True, "message": "All checks passing.", "details": r}

def gh_release_create(tag: str, title: str | None = None, notes: str | None = None, generate_notes: bool = False, draft: bool = False, prerelease: bool = False, tool_context=None) -> Dict[str, Any]:
    """
    Create a GitHub release.
    """
    repo_root = str(_configured_repo_root(tool_context))
    cmd = ["gh", "release", "create", tag]
    if title:
        cmd += ["--title", title]
    if notes:
        cmd += ["--notes", notes]
    if generate_notes:
        cmd += ["--generate-notes"]
    if draft:
        cmd += ["--draft"]
    if prerelease:
        cmd += ["--prerelease"]
    r = _run(cmd, cwd=repo_root, tool_context=tool_context)
    return r

def _parse_porcelain_paths(stdout: str) -> set:
    paths = set()
    for line in (stdout or "").splitlines():
        line = line.rstrip()
        if len(line) < 4:
            continue
        path_part = line[3:]
        if " -> " in path_part:
            # Renames: "R  OLD -> NEW" - the new path is what ends up in the diff.
            path_part = path_part.split(" -> ", 1)[1]
        paths.add(path_part.strip('"'))
    return paths

def _diff_release_against_sprint_tracking(tool_context=None) -> Dict[str, Any]:
    """
    Compares ScrumState.sprint_files_touched (US-0009) against the actual
    working-tree diff at release time (git status --porcelain), so
    completed sprint work is never silently dropped from - or an untracked
    change silently smuggled into - the release PR.
    """
    repo_root = str(_configured_repo_root(tool_context))
    tracked = set(
        tool_context.state.get("sprint_files_touched", [])
        if tool_context and getattr(tool_context, "state", None)
        else []
    )

    # --untracked-files=all: without it, a brand-new untracked directory
    # collapses to a single "dir/" line instead of listing the files inside,
    # which breaks exact-path comparison against sprint_files_touched.
    result = _run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo_root,
        tool_context=tool_context,
    )
    if result.get("status") == "error" and "returncode" not in result:
        return {
            "status": "error",
            "matched": False,
            "tracked_files": sorted(tracked),
            "changed_files": [],
            "warnings": [f"git status could not be executed: {result.get('message', 'unknown error')}"],
        }

    changed = _parse_porcelain_paths(result.get("stdout", ""))
    missing = sorted(tracked - changed)
    extra = sorted(changed - tracked)

    warnings = []
    if missing:
        warnings.append(f"Tracked as sprint-touched but missing from the release diff: {missing}")
    if extra:
        warnings.append(f"Present in the release diff but not tracked as sprint-touched: {extra}")

    return {
        "status": "ok",
        "matched": not warnings,
        "tracked_files": sorted(tracked),
        "changed_files": sorted(changed),
        "warnings": warnings,
    }

def _stage_sprint_tracked_changes(diff_check: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    """
    Selectively stages working-tree changes that match
    ScrumState.sprint_files_touched (US-0009), instead of blindly
    `git add -A`-ing everything. Changes present in the diff but not
    tracked as sprint work are left unstaged and flagged for human
    review, rather than silently swept into the release (US-0011).
    """
    repo_root = str(_configured_repo_root(tool_context))
    to_stage = sorted(set(diff_check.get("tracked_files", [])) & set(diff_check.get("changed_files", [])))
    flagged = sorted(set(diff_check.get("changed_files", [])) - set(diff_check.get("tracked_files", [])))

    staged = []
    stage_errors = []
    for path in to_stage:
        result = _run(["git", "add", "--", path], cwd=repo_root, tool_context=tool_context)
        if result.get("status") == "ok":
            staged.append(path)
        else:
            stage_errors.append({"path": path, "error": result.get("stderr") or result.get("message")})

    warnings = []
    if flagged:
        warnings.append(
            "Uncommitted changes unrelated to this sprint's tracked files were left unstaged "
            f"for human review rather than auto-committed: {flagged}"
        )

    return {
        "staged_files": staged,
        "flagged_for_review": flagged,
        "stage_errors": stage_errors,
        "warnings": warnings,
    }

def create_release_pr(title: str, body: str, branch: str = "release/increment", tool_context=None) -> Dict[str, Any]:
    """
    Create a Pull Request for the release increment.
    """
    # ISSUE-0001: "Ensure Human Review is done for each increment" had no
    # code backing it - refuse until a fresh "release" approval was
    # recorded via record_human_approval since the last release PR.
    state = tool_context.state if tool_context and getattr(tool_context, "state", None) else {}
    release_approvals = sum(1 for a in state.get("human_approvals", []) if a.get("type") == "release")
    if release_approvals <= state.get("release_approval_baseline", 0):
        return {
            "status": "error",
            "message": (
                "Cannot create a release PR before this increment has a fresh human approval - "
                "call record_human_approval('release', ...) first (see ORCHESTRATOR_PROMPT "
                "SPRINT REVIEW & RELEASE)."
            ),
        }

    sprint_tracking_check = _diff_release_against_sprint_tracking(tool_context=tool_context)
    stage_result = _stage_sprint_tracked_changes(sprint_tracking_check, tool_context=tool_context)
    # add_all=False: only the paths _stage_sprint_tracked_changes just
    # staged go into the commit - stray, untracked changes are left
    # unstaged for human review rather than swept in by `git add -A`.
    push_res = git_push(branch=branch, commit_message=f"chore: {title}", add_all=False, tool_context=tool_context)
    # Reuse push_res's (possibly eval-run-prefixed - see git_push) branch
    # rather than the raw `branch` param, so head matches what was actually
    # pushed.
    # base intentionally omitted: gh_pr_create defaults it to the
    # configured default branch (see _default_push_branch), not a
    # hardcoded "main".
    pr_res = gh_pr_create(title=title, body=body, head=push_res.get("branch", branch), tool_context=tool_context)
    # Reflect push/PR-create failures honestly instead of always claiming
    # "ok" - a caller (agent or the eval harness) that only checks this
    # top-level status has no other way to notice e.g. `gh pr create`
    # failing because `base` doesn't exist on the remote yet, which is
    # exactly what silently produced zero PRs for a whole eval run before.
    ok = push_res.get("status") == "ok" and pr_res.get("status") == "ok"
    if ok:
        # Bumping only on success mirrors retro_baseline: a failed PR
        # creation shouldn't consume this increment's approval, and
        # sprint_report_pending_release only clears once the release
        # actually went out (see ISSUE-0010).
        state["release_approval_baseline"] = release_approvals
        state["sprint_report_pending_release"] = False
    return {
        "status": "ok" if ok else "error",
        "push": push_res,
        "pr": pr_res,
        "sprint_tracking_check": sprint_tracking_check,
        "staged_files": stage_result["staged_files"],
        "flagged_for_review": stage_result["flagged_for_review"],
        "warnings": sprint_tracking_check["warnings"] + stage_result["warnings"],
    }

def _record_pr_review_call(tool_context) -> None:
    """
    Counts a gh_pr_review/gh_pr_comment call per calling role, so
    advance_story_stage's Reviewed/Tested gates (ISSUE-0005) can tell "a
    role claimed this stage" apart from "a role actually left a review
    comment on the PR".
    """
    if not tool_context or not getattr(tool_context, "state", None):
        return
    agent_name = getattr(tool_context, "agent_name", None)
    if not agent_name:
        return
    calls = dict(tool_context.state.get("pr_review_calls", {}))
    calls[agent_name] = calls.get(agent_name, 0) + 1
    tool_context.state["pr_review_calls"] = calls

def gh_pr_comment(body: str, pr_id: str | int | None = None, tool_context=None) -> Dict[str, Any]:
    """
    Add a comment to a Pull Request.
    """
    repo_root = str(_configured_repo_root(tool_context))
    agent_name = getattr(tool_context, "agent_name", "Agent")
    prefixed_body = f"**{agent_name}:** {body}"
    cmd = ["gh", "pr", "comment"]
    if pr_id:
        cmd.append(str(pr_id))
    cmd += ["--body", prefixed_body]
    r = _run(cmd, cwd=repo_root, tool_context=tool_context)
    if r.get("status") == "ok":
        _record_pr_review_call(tool_context)
    return r

def gh_pr_review(body: str, event: str = "COMMENT", pr_id: str | int | None = None, tool_context=None) -> Dict[str, Any]:
    """
    Submit a review for a Pull Request.
    - event: APPROVE, REQUEST_CHANGES, or COMMENT
    """
    repo_root = str(_configured_repo_root(tool_context))
    agent_name = getattr(tool_context, "agent_name", "Agent")
    prefixed_body = f"**{agent_name}:** {body}"
    cmd = ["gh", "pr", "review"]
    if pr_id:
        cmd.append(str(pr_id))
    cmd += ["--body", prefixed_body, f"--{event.lower()}"]
    r = _run(cmd, cwd=repo_root, tool_context=tool_context)
    if r.get("status") == "ok":
        _record_pr_review_call(tool_context)
    return r

def gh_pr_check_logs(pr_id: str | int | None = None, check_name: str | None = None, tool_context=None) -> Dict[str, Any]:
    """
    Fetch logs for a PR's CI checks.
    """
    repo_root = str(_configured_repo_root(tool_context))
    
    # 1. Get the run ID for the PR
    cmd_run_list = ["gh", "run", "list", "--limit", "1", "--json", "databaseId"]
    if pr_id:
        # If pr_id is a branch name, we can filter by it
        cmd_run_list += ["--branch", str(pr_id)]
    
    r_list = _run(cmd_run_list, cwd=repo_root, tool_context=tool_context)
    if r_list.get("status") == "error" or not r_list.get("stdout"):
        return {"status": "error", "message": "Could not find any workflow runs.", "details": r_list}
        
    try:
        runs = json.loads(r_list["stdout"])
        if not runs:
            return {"status": "error", "message": "No runs found for this PR/branch."}
        run_id = runs[0]["databaseId"]
    except Exception as e:
        return {"status": "error", "message": f"Failed to parse run list: {e}"}

    # 2. View the log
    cmd_log = ["gh", "run", "view", str(run_id), "--log"]
    r_log = _run(cmd_log, cwd=repo_root, tool_context=tool_context)
    
    return r_log

def repo_status(tool_context=None) -> Dict[str, Any]:
    """
    Return detected repo configuration and quick diagnostics.
    """
    cfg = (tool_context.state.get("repo") if tool_context and getattr(tool_context, "state", None) else None) or {}
    root = _configured_repo_root(tool_context)
    
    # Check environment configuration
    env_cfg = {
        "url": os.environ.get("GITHUB_REPO_URL"),
        "branch": os.environ.get("GITHUB_REPO_BRANCH"),
        "state_repo_path": os.environ.get("STATE_REPO_PATH"),
        "internal_mount": os.environ.get("INTERNAL_STATE_REPO_PATH"),
    }
    
    diagnostics = {
        "exists": root.exists(),
        "git_dir": (root / ".git").exists(),
        "configured_in_state": bool(cfg),
        "env_repo_url_present": bool(env_cfg["url"]),
        "using_internal_mount": bool(env_cfg["internal_mount"]),
    }

    # Check identity
    token = tool_context.state.get("github_token")
    if token:
        diagnostics["auth_method"] = "GitHub App (Token)"
        # Identify who we are
        who = _run(["gh", "api", "user"], cwd=str(root), tool_context=tool_context)
        if who.get("status") == "ok":
            try:
                user_data = json.loads(who["stdout"])
                diagnostics["identity"] = user_data.get("login")
            except Exception:
                pass
    else:
        diagnostics["auth_method"] = "Personal Account (gh CLI)"
        if tool_context.state.get("last_auto_auth_error"):
            diagnostics["auto_auth_error"] = tool_context.state.get("last_auto_auth_error")
        # Try gh auth status (non-fatal)
        gh = _run(["gh", "auth", "status"], cwd=str(root), tool_context=tool_context)
        diagnostics["gh_auth_ok"] = (gh.get("returncode") == 0)

    return {"status": "ok", "config": cfg, "env_config": env_cfg, "repo_root": str(root), "diagnostics": diagnostics}