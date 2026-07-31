# agents/scrum_team/tools/github.py
from __future__ import annotations
import os
import json
import re
from typing import Any, Dict
from .base import _configured_repo_root, _run, _default_push_branch, _develop_branch_name, _with_eval_branch_prefix, _with_eval_title_prefix
from ..helpers import required_pre_release_approval

def _ensure_remote_branch_exists(repo_root: str, branch: str, tool_context=None) -> Dict[str, Any]:
    """
    Creates+pushes `branch` from the current local HEAD if it doesn't
    already exist on origin. Used by configure_github_repo to bootstrap the
    develop/main branch pair a fresh GitFlow setup needs before any
    feature-branch PR (start_feature_branch) or sprint PR (create_release_pr)
    can target either of them.
    """
    check = _run(["git", "ls-remote", "--exit-code", "--heads", "origin", branch], cwd=repo_root, tool_context=tool_context)
    if check.get("returncode") == 0:
        return {"status": "ok", "created": False}
    _run(["git", "checkout", "-B", branch], cwd=repo_root, tool_context=tool_context)
    # A genuinely empty repo (fresh GitHub repo, zero commits) has nothing to
    # push yet - git refuses "src refspec <branch> does not match any" until
    # there's at least one commit. Same fallback git_push already uses for
    # "nothing to commit": create an empty commit so the branch can actually
    # be pushed - seed_repository's real content commit follows immediately
    # after configure_github_repo in the setup sequence anyway.
    has_commit = _run(["git", "rev-parse", "--verify", "HEAD"], cwd=repo_root, tool_context=tool_context)
    if has_commit.get("status") == "error":
        _run(["git", "commit", "--allow-empty", "-m", f"chore: initialize {branch}"], cwd=repo_root, tool_context=tool_context)
    push = _run(["git", "push", "-u", "origin", branch], cwd=repo_root, tool_context=tool_context)
    return {"status": "ok" if push.get("status") == "ok" else "error", "created": True, "push": push}


def configure_github_repo(repo_url: str, local_path: str = "", default_branch: str | None = None, develop_branch: str | None = None, tool_context=None) -> Dict[str, Any]:
    """
    Configure the GitHub repository used for persistence and tooling.
    - repo_url: SSH or HTTPS URL
    - local_path: optional existing checkout or desired clone path. If empty, will use the path from the STATE_REPO_PATH environment variable.
    - default_branch: "main" - branch sprint PRs (create_release_pr) merge
      into. Defaults to the configured default (see _default_push_branch)
      rather than a hardcoded "main", so a caller-omitted value can't clobber
      an eval/test run's GITHUB_REPO_BRANCH-configured branch.
    - develop_branch: integration branch feature-branch PRs
      (start_feature_branch) merge into. Defaults to the configured default
      (see _develop_branch_name) the same way.
    This will clone the repo if local_path does not exist, then ensure both
    branches exist remotely (GitFlow bootstrap) - main first (from whatever
    the clone's current HEAD is), then develop branched from main, so a
    brand-new/empty repo ends up with both pointing at the same initial
    commit before seed_repository's bootstrap content lands on develop next.
    """
    from pathlib import Path
    from .base import _project_root

    default_branch = default_branch or _default_push_branch(tool_context)
    develop_branch = develop_branch or _develop_branch_name(tool_context)
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

    repo_root_str = str(target_dir)
    main_bootstrap = _ensure_remote_branch_exists(repo_root_str, default_branch, tool_context=tool_context)
    # Whether default_branch was just created above or already existed,
    # checkout ensures local HEAD is on it before branching develop from it.
    _run(["git", "checkout", default_branch], cwd=repo_root_str, tool_context=tool_context)
    develop_bootstrap = _ensure_remote_branch_exists(repo_root_str, develop_branch, tool_context=tool_context)

    # Save config into session.state
    repo_cfg = {
        "url": repo_url,
        "local_path": str(target_dir),
        "default_branch": default_branch,
        "develop_branch": develop_branch,
    }
    tool_context.state["repo"] = repo_cfg
    ok = main_bootstrap.get("status") == "ok" and develop_bootstrap.get("status") == "ok"
    return {
        "status": "ok" if ok else "error",
        "repo": repo_cfg,
        "main_bootstrap": main_bootstrap,
        "develop_bootstrap": develop_bootstrap,
    }

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
    - allow_protected: escape hatch for the legitimate direct-push cases
      (seed_repository's initial bootstrap commit onto develop, and the
      mechanical roadmap sync when the sprint budget runs out - see
      _sync_and_commit_roadmap_on_exhaustion in agent.py). Defaults False -
      see ISSUE-0006: DEV_PROMPT's "the configured default branch is
      PROTECTED... cannot push to it directly" had no code backing it at
      all before this.
    """
    branch = _with_eval_branch_prefix(branch)
    # Reject anything that isn't a plain branch name before the protected-
    # branch check even looks at it. Without this, branch="HEAD:main" (or
    # any other src:dst refspec) never equals the protected-branch string
    # "main", so the check below passes - but `git push origin HEAD:main`
    # still pushes current HEAD straight onto main, bypassing the guard
    # entirely (see GH issue #104). Valid branch names never contain ':',
    # so this can only reject a bypass attempt, never a legitimate branch.
    if not re.match(r"^[A-Za-z0-9._/-]+$", branch):
        return {
            "status": "error",
            "message": (
                f"Refusing to push to '{branch}' - not a plain branch name (refspec/shell "
                "metacharacters like ':' are not allowed). Pass a real branch name, not a refspec."
            ),
        }
    # GitFlow: both main (_default_push_branch) and develop are protected -
    # only feature branches get pushed to directly; everything else reaches
    # develop/main via a PR merge (start_feature_branch/merge_story_pr,
    # create_release_pr).
    protected_branches = {_default_push_branch(tool_context), _develop_branch_name(tool_context)}
    if branch in protected_branches and not allow_protected:
        return {
            "status": "error",
            "message": (
                f"Refusing to push directly to '{branch}' - it's one of this repo's protected "
                f"branches ({sorted(protected_branches)}, see DEV_PROMPT). Push to a feature branch "
                "and open a Pull Request via start_feature_branch/gh_pr_create/create_release_pr instead."
            ),
        }
    repo_root = str(_configured_repo_root(tool_context))

    # Ensure branch exists locally - fatal if this fails: continuing to
    # commit/push against whatever branch was previously checked out would
    # silently write the change somewhere other than the caller's intended
    # target (see GH issue #104 - this used to discard the result entirely).
    checkout = _run(["git", "checkout", "-B", branch], cwd=repo_root, tool_context=tool_context)
    if checkout.get("status") == "error":
        return {"status": "error", "message": f"Could not check out branch '{branch}': {checkout.get('stderr') or checkout.get('message')}", "steps": {"checkout": checkout}}

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

    return {"status": "ok" if r3.get("status") == "ok" else "error", "branch": branch, "steps": {"checkout": checkout, "add": r1, "commit": r2, "push": r3}}

def gh_pr_create(title: str, body: str = "", base: str | None = None, head: str | None = None, draft: bool = False, head_is_resolved: bool = False, tool_context=None) -> Dict[str, Any]:
    """
    Create a Pull Request using `gh` CLI. Assumes authentication is set up.
    - base: target branch. Defaults to the configured default branch (see
      _default_push_branch) rather than a hardcoded "main", so an isolated
      eval/test run can target its own branch via GITHUB_REPO_BRANCH.
    - head: source branch (defaults to current if None). In an eval run
      (EVAL_RUN_ID set), auto-tagged with the run id the same way git_push
      tags branches, so a head passed as the plain (unprefixed) branch name
      still resolves - see _with_eval_branch_prefix. No-op in real usage.
    - head_is_resolved: set True when head is already a fully-resolved
      branch name (e.g. develop/main from _develop_branch_name/
      _default_push_branch, which bake any eval run id directly into the
      value rather than via _with_eval_branch_prefix) - skips the
      auto-tagging above so it isn't double-prefixed. Defaults False since
      most callers pass an ad-hoc branch name (e.g. a feature branch).
    - draft: open PR as draft
    """
    repo_root = str(_configured_repo_root(tool_context))
    base = base or _default_push_branch(tool_context)
    if head and not head_is_resolved:
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

def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "story"

def start_feature_branch(story_id: str, slug: str, tool_context=None) -> Dict[str, Any]:
    """
    GitFlow: opens the per-story feature branch + draft PR into develop.
    Call this before writing any code for a story. Checks out+pulls develop
    fresh, branches feature/<story_id>-<slug> off it, pushes it (sweeping in
    any already-written-but-uncommitted files, e.g. a story markdown file PO
    just authored via upsert_story), then opens a draft PR back into develop.
    Records the resulting branch name in state
    (active_feature_branches[story_id]) so mark_pr_ready_for_review/
    merge_story_pr don't have to re-derive it.
    - story_id: the story's ID (e.g. "US-0012").
    - slug: short human-readable description (e.g. "add-login"); sanitized
      to lowercase-hyphenated automatically.
    """
    repo_root = str(_configured_repo_root(tool_context))
    develop = _develop_branch_name(tool_context)
    branch = f"feature/{story_id}-{_slugify(slug)}"

    fetch = _run(["git", "fetch", "origin", develop], cwd=repo_root, tool_context=tool_context)
    checkout_develop = _run(["git", "checkout", "-B", develop, f"origin/{develop}"], cwd=repo_root, tool_context=tool_context)
    if checkout_develop.get("status") == "error":
        return {
            "status": "error",
            "message": f"Could not check out '{develop}': {checkout_develop.get('stderr') or checkout_develop.get('message')}",
            "fetch": fetch,
        }

    checkout_feature = _run(["git", "checkout", "-B", branch], cwd=repo_root, tool_context=tool_context)
    if checkout_feature.get("status") == "error":
        return {
            "status": "error",
            "message": f"Could not create branch '{branch}': {checkout_feature.get('stderr') or checkout_feature.get('message')}",
        }

    push_res = git_push(branch=branch, commit_message=f"chore: start {story_id}", tool_context=tool_context)
    # git_push already applied any eval-run prefix (_with_eval_branch_prefix) -
    # use its reported branch as the PR head, and tell gh_pr_create it's
    # already resolved so it doesn't try to re-tag it.
    actual_branch = push_res.get("branch", branch)

    pr_res = gh_pr_create(
        title=f"{story_id}: {slug}",
        body=f"Implements {story_id}. Opened by start_feature_branch as a draft - ready for review once implementation is complete (see mark_pr_ready_for_review).",
        base=develop,
        head=actual_branch,
        head_is_resolved=True,
        draft=True,
        tool_context=tool_context,
    )

    ok = push_res.get("status") == "ok" and pr_res.get("status") == "ok"
    if ok and tool_context and getattr(tool_context, "state", None):
        active = dict(tool_context.state.get("active_feature_branches", {}))
        active[story_id] = actual_branch
        tool_context.state["active_feature_branches"] = active

    return {
        "status": "ok" if ok else "error",
        "branch": actual_branch,
        "push": push_res,
        "pr": pr_res,
    }

def mark_pr_ready_for_review(pr_id: str | int | None = None, tool_context=None) -> Dict[str, Any]:
    """
    GitFlow: removes draft status from a PR opened via start_feature_branch,
    once implementation is complete and CI is green (see gh_pr_checks).
    - pr_id: PR number, URL, or branch. If None, uses the current branch's PR.
    """
    repo_root = str(_configured_repo_root(tool_context))
    cmd = ["gh", "pr", "ready"]
    if pr_id:
        cmd.append(str(pr_id))
    return _run(cmd, cwd=repo_root, tool_context=tool_context)

def merge_story_pr(pr_id: str | int | None = None, admin: bool = False, tool_context=None) -> Dict[str, Any]:
    """
    GitFlow: merges a story's feature-branch PR into develop. Call this as
    QA, after advance_story_stage(..., "Tested") succeeds.
    - pr_id: PR number, URL, or branch. If None, uses the current branch's PR.
    - admin: bypass required-checks/reviews (--admin). Defaults False - a
      story-level merge should respect real branch-protection like any
      normal PR merge; forced-admin merges are for the eval harness's own
      sprint-level (develop->main) automation, not this.
    """
    repo_root = str(_configured_repo_root(tool_context))
    cmd = ["gh", "pr", "merge"]
    if pr_id:
        cmd.append(str(pr_id))
    cmd.append("--merge")
    if admin:
        cmd.append("--admin")
    return _run(cmd, cwd=repo_root, tool_context=tool_context)

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

def create_release_pr(title: str, body: str, tool_context=None) -> Dict[str, Any]:
    """
    GitFlow sprint PR: develop -> main (see _develop_branch_name/
    _default_push_branch). Under GitFlow, sprint work already landed on
    develop via individually merged feature-branch PRs (start_feature_branch/
    merge_story_pr) - there's no loose local diff to reconcile/stage here
    anymore, just the integration PR itself.
    """
    # ISSUE-0001: "Ensure Human Review is done for each increment" had no
    # code backing it - refuse until a fresh approval was recorded via
    # record_human_approval since the last release PR. Which approval type
    # (if any) is required depends on INTERACTION_LEVEL (see
    # docs/INTERACTION-LEVELS.md) - e.g. none at all at the CEO/EVAL levels,
    # where a human doesn't review each release individually.
    state = tool_context.state if tool_context and getattr(tool_context, "state", None) else {}
    required_approval = required_pre_release_approval()
    release_approvals = None
    if required_approval:
        release_approvals = sum(1 for a in state.get("human_approvals", []) if a.get("type") == required_approval)
        if release_approvals <= state.get("release_approval_baseline", 0):
            message = (
                f"Cannot create a release PR - this interaction level requires a fresh "
                f"'{required_approval}' human approval for this increment - call "
                f"record_human_approval('{required_approval}', ...) first (see "
                "docs/INTERACTION-LEVELS.md)."
            )
            from .notifications import record_blocking_interaction
            record_blocking_interaction(
                "approval",
                f"Release PR '{title}' is waiting on a '{required_approval}' human approval.",
                detail=message,
                tool_context=tool_context,
            )
            return {"status": "error", "message": message}

    repo_root = str(_configured_repo_root(tool_context))
    develop = _develop_branch_name(tool_context)
    default_branch = _default_push_branch(tool_context)
    fetch_res = _run(["git", "fetch", "origin", develop], cwd=repo_root, tool_context=tool_context)

    # base/head are both already fully-resolved branch names (an eval run
    # bakes its run id directly into GITHUB_REPO_BRANCH/GITHUB_DEVELOP_BRANCH
    # rather than via _with_eval_branch_prefix - see _develop_branch_name) -
    # head_is_resolved=True so gh_pr_create doesn't re-tag it.
    pr_res = gh_pr_create(
        title=title,
        body=body,
        base=default_branch,
        head=develop,
        head_is_resolved=True,
        tool_context=tool_context,
    )
    # Reflect PR-create failures honestly instead of always claiming "ok" -
    # a caller (agent or the eval harness) that only checks this top-level
    # status has no other way to notice e.g. `gh pr create` failing because
    # `base` doesn't exist on the remote yet.
    ok = pr_res.get("status") == "ok"
    if ok:
        # Bumping only on success mirrors retro_baseline: a failed PR
        # creation shouldn't consume this increment's approval, and
        # sprint_report_pending_release only clears once the release
        # actually went out (see ISSUE-0010).
        if required_approval and release_approvals is not None:
            state["release_approval_baseline"] = release_approvals
        state["sprint_report_pending_release"] = False
    return {
        "status": "ok" if ok else "error",
        "fetch": fetch_res,
        "pr": pr_res,
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