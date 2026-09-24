# agents/scrum_team/tools/github.py
from __future__ import annotations
import os
import json
import re
from typing import Any, Dict
from .base import _configured_repo_root, _run, _default_push_branch, _develop_branch_name, _with_eval_branch_prefix, _with_eval_title_prefix
from ..helpers import (
    required_pre_release_approval,
    required_pre_implementation_approval,
    sprint_backlog_pr_missing,
    ready_backlog_shortfall,
    get_interaction_level,
)

def story_spec_pr_merged(story_id: str, tool_context=None) -> bool:
    """
    True if this story's own create_story_spec_pr branch (story-spec/<id>)
    has actually merged - the evidence record_design_approval requires at
    the Stakeholder interaction level (see agents/scrum_team/tools/
    requirements.py) instead of trusting the model's own assertion that a
    stakeholder reviewed the story's spec. False on any lookup failure
    (branch never existed, `gh` unreachable, etc.) - the caller decides
    what to do with "not merged", this never raises.
    """
    repo_root = str(_configured_repo_root(tool_context))
    branch = _with_eval_branch_prefix(f"story-spec/{story_id}")
    result = _run(["gh", "pr", "view", branch, "--json", "state"], cwd=repo_root, tool_context=tool_context)
    if result.get("status") != "ok":
        return False
    try:
        data = json.loads(result.get("stdout", "") or "{}")
    except Exception:
        return False
    return data.get("state") == "MERGED"


def release_pr_still_open(tool_context=None) -> bool:
    """
    True if a release PR (develop -> main, or their eval-run-resolved
    equivalents) is currently open and unmerged - the evidence start_sprint
    (tools/scrum.py) requires before planning the next increment. False on
    any lookup failure or genuinely no open PR, never raises - mirrors
    story_spec_pr_merged's own fail-safe shape.

    create_release_pr never merges anything itself (see its own docstring)
    - merging main is always an out-of-band human/external action, at every
    interaction level - and it clears sprint_report_pending_release the
    instant the PR is *opened*, not merged. A real incident: a new sprint
    got planned while the previous one's release PR was still sitting open,
    because every existing gate (new_sprint_item_blocked) only checks that
    flag plus whether stories reached Accepted - both already true the
    moment the PR opens, well before anyone merges it.

    Checks for a currently-OPEN PR rather than walking ancestry
    (`git merge-base --is-ancestor origin/develop origin/main`) deliberately -
    a squash or rebase merge (GitHub's other two strategies, either of
    which a human reviewer might actually use) rewrites history, so
    develop's tip is never an ancestor of main's afterward; only a real
    merge commit preserves that link. "Is there an open PR right now" has
    no such blind spot regardless of which merge strategy gets used, and
    needs no separate git-vs-gh reconciliation.
    """
    repo_root = str(_configured_repo_root(tool_context))
    develop = _develop_branch_name(tool_context)
    default_branch = _default_push_branch(tool_context)
    result = _run(
        ["gh", "pr", "list", "--base", default_branch, "--head", develop, "--state", "open", "--json", "number"],
        cwd=repo_root, tool_context=tool_context,
    )
    if result.get("status") != "ok":
        return False
    try:
        data = json.loads(result.get("stdout", "") or "[]")
    except Exception:
        return False
    return bool(data)


# Paths the scrum tools themselves write to outside of an explicit git_push
# call - upsert_story/upsert_epic/upsert_issue/update_roadmap/upsert_prd/
# upsert_srs/upsert_adr write under specs/, save_state_to_repo writes
# .hc/state.json. Deliberately NOT "-A"/the whole working tree: this repo is
# also where DevTeam's own uncommitted source-code edits live between
# start_feature_branch calls, and an automatic recovery step has no business
# sweeping those into an unrelated "planning docs" commit.
_OPEN_CHANGES_PATHS = ["specs", ".hc"]


def integrate_open_changes(tool_context=None) -> Dict[str, Any]:
    """
    Commits any uncommitted writes under specs/ and .hc/state.json to the
    current branch, scoped to just those paths.

    upsert_story/upsert_epic/upsert_issue/update_roadmap and friends only
    ever write files to disk - by design, see save_state_to_repo's own
    docstring ("purely local safety net") - none of them commit. Every
    story/roadmap edit sits as a dangling uncommitted change in the shared
    working tree until whatever tool happens to run the first
    `git add` + commit. A real incident: after a session got interrupted
    (token exhaustion) mid sprint-planning and was resumed, `git checkout -B
    develop origin/develop` - the first step of every create_story_spec_pr/
    create_sprint_backlog_pr/start_feature_branch call - kept failing with
    "local changes would be overwritten", because that planning output was
    still sitting there uncommitted. Nothing could recover on its own since
    committing it was gated behind a checkout that could never succeed
    without first committing it. This is the fix, and it's also what
    create_story_spec_pr/create_sprint_backlog_pr/start_feature_branch now
    call automatically (see _checkout_develop_or_recover below) the moment
    that exact failure happens, instead of just giving up - call it directly
    only when recovering a stuck session by hand (e.g. right after
    re-running init_scrum_state to rebuild state from the specs/ already
    persisted in the repo).
    """
    repo_root_path = _configured_repo_root(tool_context)
    repo_root = str(repo_root_path)
    # A pathspec git add can't match at all (e.g. .hc/ before any
    # save_state_to_repo call has ever run yet) hard-fails the whole
    # command ("did not match any files") rather than just skipping it the
    # way git status does - only pass pathspecs that currently exist.
    existing_paths = [p for p in _OPEN_CHANGES_PATHS if (repo_root_path / p).exists()]
    if not existing_paths:
        return {"status": "ok", "integrated": False, "message": "No open planning-doc changes under specs/ or .hc/ to integrate."}

    status = _run(["git", "status", "--porcelain", "--"] + existing_paths, cwd=repo_root, tool_context=tool_context)
    if status.get("status") != "ok":
        return {"status": "error", "message": "Could not read git status.", "git_status": status}
    if not status.get("stdout"):
        return {"status": "ok", "integrated": False, "message": "No open planning-doc changes under specs/ or .hc/ to integrate."}

    add_res = _run(["git", "add", "--"] + existing_paths, cwd=repo_root, tool_context=tool_context)
    if add_res.get("status") != "ok":
        return {"status": "error", "message": "Failed to stage open planning-doc changes.", "add": add_res}

    staged = _run(["git", "diff", "--cached", "--name-only"], cwd=repo_root, tool_context=tool_context)
    files = [line for line in staged.get("stdout", "").splitlines() if line.strip()]
    if not files:
        return {"status": "ok", "integrated": False, "message": "No open planning-doc changes under specs/ or .hc/ to integrate."}

    commit = _run(
        ["git", "commit", "-m", "chore: integrate open scrum planning-doc changes"],
        cwd=repo_root, tool_context=tool_context,
    )
    if commit.get("status") != "ok":
        return {"status": "error", "message": "Failed to commit open planning-doc changes.", "files": files, "commit": commit}
    return {"status": "ok", "integrated": True, "files": files, "commit": commit}


def _preserve_local_only_develop_commits(repo_root: str, develop: str, tool_context=None) -> Dict[str, Any] | None:
    """
    ISSUE-0050 / 0.1.0-run33, 0.1.0-run34: _checkout_develop_or_recover's own
    `git checkout -B <develop> origin/<develop>` (right after this call)
    unconditionally resets the local branch to match origin - confirmed via a
    live repro to silently discard any commits that exist locally but were
    never pushed, including save_state_to_repo's own local-only checkpoint
    commits (see its docstring: "Never pushes ... a purely local safety
    net"). Two real eval runs hit exactly this: a story that had genuinely
    progressed all the way to Accepted in-session (each stage transition its
    own local-only checkpoint commit) regressed to whatever stage was last
    actually pushed, the next time ANY of create_story_spec_pr/
    create_sprint_backlog_pr/start_feature_branch/create_release_pr ran this
    shared helper (all four do, and do so often - once per story spec, once
    per sprint backlog, every feature branch, every release attempt) while
    that story's own progress hadn't yet been folded into an actual push.
    This then deadlocked the one-story-at-a-time sequential stage gate
    (advance_story_stage) against a story the team had already, as far as it
    knew, shipped - burning a sprint's entire token budget on blocked
    retries.

    Called right after the caller's own `git fetch origin <develop>`, before
    the destructive reset-checkout. Best-effort, in order:
    - No local <develop> yet, or it isn't ahead of origin/<develop> (nothing
      the reset below would actually discard) - no-op.
    - Local is a clean fast-forward ahead of origin (the common case - nothing
      else has been pushed to <develop> since this clone last synced) - just
      push it. The reset-checkout that follows is now a true no-op.
    - Local and origin have genuinely diverged (e.g. a story-spec PR merged
      server-side while local also advanced) - merge origin/<develop> into
      local <develop> first. A clean merge (the common case in practice -
      different files touched: a story's own state/markdown vs. another
      story's brand new spec file) still gets pushed, again making the
      reset-checkout that follows a no-op.
    - A real, unresolvable merge conflict can't be handled safely here -
      abort the merge, preserve the pre-merge local tip under a pushed
      rescue branch (so the commits stay reachable and recoverable instead
      of silently vanishing the moment the caller's reset-checkout runs),
      and log a decision_log entry naming it. The caller's original
      reset-checkout still proceeds after this - a logged, recoverable loss
      instead of a silent one, since auto-resolving a real content conflict
      isn't safe to attempt unattended.

    Returns None if there was nothing to preserve (safe to skip - the usual
    case). Otherwise a dict with "status" ("ok" if local now matches origin,
    "error" if a rescue branch had to be used instead) and "in_sync" (True
    once local ahead-of-origin commits are also now on origin, meaning the
    caller's reset-checkout is provably a no-op).
    """
    branch_check = _run(["git", "rev-parse", "--verify", "--quiet", develop], cwd=repo_root, tool_context=tool_context)
    if branch_check.get("returncode") != 0:
        return None  # no local <develop> yet - nothing to lose

    ahead = _run(["git", "rev-list", f"origin/{develop}..{develop}"], cwd=repo_root, tool_context=tool_context)
    if not (ahead.get("stdout") or "").strip():
        return None  # local has nothing origin doesn't already have

    push = _run(["git", "push", "origin", develop], cwd=repo_root, tool_context=tool_context)
    if push.get("status") == "ok":
        return {"status": "ok", "in_sync": True, "action": "pushed_local_ahead"}

    # Not a plain fast-forward - local and origin have diverged. Reconcile
    # via a merge rather than letting the caller's reset-checkout discard
    # local's commits outright.
    pre_merge_tip = (_run(["git", "rev-parse", develop], cwd=repo_root, tool_context=tool_context).get("stdout") or "").strip()
    _run(["git", "checkout", develop], cwd=repo_root, tool_context=tool_context)
    merge = _run(["git", "merge", "--no-edit", f"origin/{develop}"], cwd=repo_root, tool_context=tool_context)
    if merge.get("status") == "ok":
        push_merged = _run(["git", "push", "origin", develop], cwd=repo_root, tool_context=tool_context)
        if push_merged.get("status") == "ok":
            return {"status": "ok", "in_sync": True, "action": "merged_and_pushed"}
        # Merged locally but couldn't push (race, auth, network, ...) - the
        # merge commit is still real and still contains everything; leave it
        # as-is (not in_sync, so the caller keeps its normal reset-checkout,
        # which will discard this local merge same as before - a real
        # network/auth problem needs a human regardless).
        return {"status": "error", "in_sync": False, "action": "merged_but_push_failed"}

    _run(["git", "merge", "--abort"], cwd=repo_root, tool_context=tool_context)
    rescue_branch = f"rescued-{develop.replace('/', '-')}-{pre_merge_tip[:8] or 'unknown'}"
    _run(["git", "branch", rescue_branch, pre_merge_tip], cwd=repo_root, tool_context=tool_context)
    rescue_push = _run(["git", "push", "origin", rescue_branch], cwd=repo_root, tool_context=tool_context)
    if tool_context is not None and getattr(tool_context, "state", None) is not None:
        try:
            s = tool_context.state
            pushed_note = "pushed" if rescue_push.get("status") == "ok" else "push failed - local only, may not survive"
            s["decision_log"] = list(s.get("decision_log", [])) + [{
                "title": f"Local-only {develop} progress could not be auto-reconciled with origin",
                "decision": (
                    f"Preserved the pre-reset local tip ({pre_merge_tip[:8] or 'unknown'}) as branch "
                    f"'{rescue_branch}' ({pushed_note}) before {develop} was reset to origin/{develop}."
                ),
                "rationale": (
                    "ISSUE-0050: a real merge conflict between local-only progress (e.g. a story's "
                    "own stage-transition state) and origin's own newer commits couldn't be "
                    "auto-resolved safely - see the rescue branch to recover it by hand."
                ),
                "owner": "system",
            }]
        except Exception:
            pass
    return {"status": "error", "in_sync": False, "action": "rescued", "rescue_branch": rescue_branch}


def _checkout_develop_or_recover(repo_root: str, develop: str, tool_context=None) -> Dict[str, Any]:
    """
    Shared `git fetch` + `git checkout -B <develop> origin/<develop>` used by
    create_story_spec_pr/create_sprint_backlog_pr/start_feature_branch/
    create_release_pr before branching off (or landing onto) develop. On the
    specific "local changes would be overwritten" failure, self-heals by
    calling integrate_open_changes() (committing dangling specs/.hc writes
    out of the way) and retrying the checkout once, rather than leaving the
    session stuck the way a real incident did (see integrate_open_changes'
    docstring) - any other checkout failure (network, auth, ...) is returned
    as-is, unretried.

    Before that reset-checkout - which unconditionally overwrites local
    <develop> with origin/<develop> - _preserve_local_only_develop_commits
    (ISSUE-0050) gets a chance to push (or merge-then-push) any commits local
    has that origin doesn't, so the reset is provably a no-op instead of a
    silent discard. See that function's own docstring for the real incident
    this fixes.

    Returns {"status", "fetch", "checkout", "auto_integrated",
    "preserved_local_commits"} - callers should treat a non-"ok" "checkout"
    the same as before this helper existed; "auto_integrated" (the
    integrate_open_changes() result, or None if recovery was never
    attempted) is extra context worth surfacing to the caller's own error
    message when present.
    """
    fetch = _run(["git", "fetch", "origin", develop], cwd=repo_root, tool_context=tool_context)
    preserved = _preserve_local_only_develop_commits(repo_root, develop, tool_context=tool_context)
    if preserved is not None and preserved.get("in_sync"):
        # Local <develop> was just pushed (or merged-then-pushed) to exactly
        # match origin/<develop> - a plain checkout keeps it there. Using
        # the caller's usual reset-checkout here instead would be harmless
        # (a no-op, since they now match) but a plain checkout also avoids
        # any risk of a race between this push and the reset re-reading a
        # not-yet-visible origin ref.
        checkout = _run(["git", "checkout", develop], cwd=repo_root, tool_context=tool_context)
    else:
        checkout = _run(["git", "checkout", "-B", develop, f"origin/{develop}"], cwd=repo_root, tool_context=tool_context)
    auto_integrated = None
    if checkout.get("status") == "error" and "would be overwritten" in (checkout.get("stderr") or "").lower():
        auto_integrated = integrate_open_changes(tool_context)
        if auto_integrated.get("integrated"):
            checkout = _run(["git", "checkout", "-B", develop, f"origin/{develop}"], cwd=repo_root, tool_context=tool_context)
    return {
        "status": checkout.get("status"),
        "fetch": fetch,
        "checkout": checkout,
        "auto_integrated": auto_integrated,
        "preserved_local_commits": preserved,
    }


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

def git_push(branch: str, commit_message: str = "chore: update", add_all: bool = True, tool_context=None) -> Dict[str, Any]:
    """
    Stage changes (optionally), commit, and push the current working tree to
    the given branch. Non-interactive; returns command outputs.
    - In an eval run (EVAL_RUN_ID set), branch is auto-tagged with the run id
      (e.g. "eval-<run-id>/<branch>") so branches from different runs sharing
      one eval repo stay distinguishable - see _with_eval_branch_prefix. No-op
      in real usage.

    This is the tool exposed to agents - it can never push to a protected
    branch (see _git_push_impl's allow_protected, which this always passes
    as False). A real ADK eval run showed the live model itself choosing to
    bypass branch protection when a user's prompt applied enough pressure
    ("skip the PR, we need this live right now") - if allow_protected were a
    parameter on this function, that's a real, settable escape hatch an LLM
    can be talked into using. The two genuinely legitimate direct-push cases
    (seed_repository's initial bootstrap commit, and the mechanical roadmap
    sync in agent.py's _sync_and_commit_roadmap_on_exhaustion) are both
    internal Python code, never an agent-issued tool call - they call
    _git_push_impl directly instead, which isn't registered as a tool for
    any role and therefore isn't something any prompt can reach at all.
    """
    return _git_push_impl(branch, commit_message, add_all, allow_protected=False, tool_context=tool_context)


def _git_push_impl(branch: str, commit_message: str = "chore: update", add_all: bool = True, allow_protected: bool = False, tool_context=None) -> Dict[str, Any]:
    """
    Real implementation behind git_push - see that function's docstring for
    why allow_protected is deliberately NOT exposed there. Call this
    directly (never as an agent tool - it isn't registered as one anywhere)
    only from internal Python code that genuinely needs to push straight to
    a protected branch: seed_repository's initial bootstrap commit onto
    develop, and the mechanical roadmap sync when the sprint budget runs out
    (_sync_and_commit_roadmap_on_exhaustion in agent.py). Defaults False -
    see ISSUE-0006: DEV_PROMPT's "the configured default branch is
    PROTECTED... cannot push to it directly" had no code backing it at all
    before this.
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
    # ISSUE-0050 follow-up / 0.1.0-run34: whether there's actually anything
    # staged to commit is checked via `git diff --cached --quiet` plumbing
    # (exit 0 = nothing staged, 1 = something staged) BEFORE attempting a
    # real commit, rather than attempting one and then string-matching
    # git's human-readable failure text afterward. Git prints two entirely
    # different messages for "nothing to commit" depending on whether any
    # untracked files happen to be lying around - "nothing to commit,
    # working tree clean" with none, "nothing added to commit but untracked
    # files present" with any - and the old code only ever matched the
    # first. A real eval run (0.1.0-run34) called this with add_all=False
    # right after integrate_open_changes (github.py's create_release_pr)
    # had already committed every real specs/.hc change itself - leaving
    # nothing staged, but leaving check_build()'s own `.coverage`/
    # `__pycache__/` test artifacts sitting untracked in the working tree
    # (this scenario repo has no .gitignore at all) - which is the SECOND
    # git message, not the first. The old substring check missed it
    # entirely, `git commit` hard-failed, and create_release_pr reported
    # this as a real error - every single release PR attempt in that run
    # failed this way, in every sprint, with no release ever actually
    # shipping. A plumbing exit-code check catches both cases uniformly and
    # doesn't depend on git's message wording (which can also vary by
    # locale) at all.
    staged_check = _run(["git", "diff", "--cached", "--quiet"], cwd=repo_root, tool_context=tool_context)
    nothing_staged = staged_check.get("returncode") == 0
    if nothing_staged:
        r2 = _run(["git", "commit", "--allow-empty", "-m", commit_message], cwd=repo_root, tool_context=tool_context)
    else:
        r2 = _run(["git", "commit", "-m", commit_message], cwd=repo_root, tool_context=tool_context)
    if r2.get("returncode") != 0:
        # A commit failure at this point is for some other, real reason
        # (nothing left to swallow - the "nothing staged" case above always
        # already retried with --allow-empty) and must be fatal here, not
        # silently continued past (see GH issue #115) - previously the
        # final status was derived only from the push result, so if the
        # remote happened to already be up to date, `git push` exits 0
        # ("Everything up-to-date") and git_push reported "ok" even though
        # the intended commit never actually happened anywhere, locally or
        # remotely.
        return {
            "status": "error",
            "message": f"git commit failed: {r2.get('stderr') or r2.get('stdout') or 'unknown error'}",
            "branch": branch,
            "steps": {"checkout": checkout, "add": r1, "commit": r2},
        }
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
    state = tool_context.state if tool_context and getattr(tool_context, "state", None) else {}
    missing_msg = sprint_backlog_pr_missing(state)
    if missing_msg:
        return {"status": "error", "message": missing_msg}

    repo_root = str(_configured_repo_root(tool_context))
    develop = _develop_branch_name(tool_context)
    branch = f"feature/{story_id}-{_slugify(slug)}"

    recovery = _checkout_develop_or_recover(repo_root, develop, tool_context=tool_context)
    checkout_develop = recovery["checkout"]
    if checkout_develop.get("status") == "error":
        return {
            "status": "error",
            "message": f"Could not check out '{develop}': {checkout_develop.get('stderr') or checkout_develop.get('message')}",
            "fetch": recovery["fetch"],
            "auto_integrated": recovery["auto_integrated"],
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

def create_sprint_backlog_pr(title: str = None, body: str = None, tool_context=None) -> Dict[str, Any]:
    """
    GitFlow: commits+pushes this sprint's planning output (roadmap, PRD,
    epics, stories reaching Ready - whatever Product Owner has written via
    upsert_prd/upsert_story/upsert_epic/update_roadmap since the last
    sprint) to its own branch, opens a PR titled "Sprint Backlog #<N>"
    against develop, and merges it immediately.

    A real eval run showed why this needs to be its own explicit step:
    upsert_story/upsert_epic/update_roadmap/upsert_prd only write files to
    disk - none of them commit, let alone push (see save_state_to_repo's
    own docstring: "purely local safety net"). Every agent shares one local
    checkout, so those dangling writes just sat there until whatever tool
    call did the FIRST `git add -A` + push - in practice DevTeam's
    start_feature_branch, which exists to sweep in small stray files like a
    single story markdown, but ended up dragging the entire roadmap/PRD
    along too and landing it on a feature branch instead of develop. Call
    this once sprint planning (this sprint's stories reaching Ready) is
    done, and BEFORE Dev Team opens the first feature branch - so planning
    output lands on develop as its own reviewable unit.

    - title/body: optional extra text appended to the PR title/body. The
      title is always prefixed "Sprint Backlog #<N>" (N = state.sprint_number,
      set by start_sprint - this refuses to run if no sprint has been
      started yet).
    """
    state = tool_context.state if tool_context and getattr(tool_context, "state", None) else {}
    sprint_number = state.get("sprint_number", 0)
    if sprint_number <= 0:
        return {
            "status": "error",
            "message": (
                "Cannot create a Sprint Backlog PR - no sprint has been started yet "
                "(sprint_number is unset). Ask Scrum Master to call start_sprint(goal) first."
            ),
        }

    # A real eval run showed "start implementing" was only ever a prompt
    # instruction away from "the backlog barely has any Ready work at all" -
    # this is the single choke point (Dev Team can't start until this PR
    # merges - see sprint_backlog_pr_missing) that mechanically forces
    # Product Owner back into the requirements-engineering loop
    # (upsert_epic/upsert_story/advance_story_stage) instead of publishing a
    # thin sprint.
    shortfall = ready_backlog_shortfall(state.get("product_backlog", []), state.get("backlog_scope_complete", False))
    if shortfall > 0:
        return {
            "status": "error",
            "message": (
                f"Cannot create the Sprint Backlog PR yet - the Ready backlog is {shortfall} "
                "stories short of holding enough work queued up (see TARGET_STORIES_PER_SPRINT x "
                "READY_BACKLOG_SPRINTS_TARGET in helpers.py). Keep running the requirements "
                "engineering loop - upsert_prd/upsert_srs/update_roadmap/upsert_epic/upsert_story, "
                "then advance_story_stage(..., 'Ready') - until enough stories are Ready, then retry. "
                "If the product's real remaining scope is genuinely smaller than this target (ISSUE-0046: "
                "never invent filler/'buffer' stories just to clear it), call "
                "declare_backlog_scope_complete(justification) instead."
            ),
        }

    repo_root = str(_configured_repo_root(tool_context))
    develop = _develop_branch_name(tool_context)
    branch = f"sprint-backlog/{sprint_number}"
    actual_branch = _with_eval_branch_prefix(branch)

    # Idempotent re-call: if this sprint's backlog PR was already opened
    # (e.g. a prior call pushed+opened it but had to stop short of merging
    # because a required human approval wasn't recorded yet - see below),
    # don't re-push/re-create it - just check whether it can merge now.
    existing_pr = _run(
        ["gh", "pr", "view", actual_branch, "--json", "number,state"],
        cwd=repo_root, tool_context=tool_context,
    )
    already_open = False
    if existing_pr.get("status") == "ok":
        try:
            already_open = bool(json.loads(existing_pr.get("stdout") or "{}").get("number"))
        except Exception:
            already_open = False

    push_res = None
    pr_res = None
    if not already_open:
        recovery = _checkout_develop_or_recover(repo_root, develop, tool_context=tool_context)
        checkout_develop = recovery["checkout"]
        if checkout_develop.get("status") == "error":
            return {
                "status": "error",
                "message": f"Could not check out '{develop}': {checkout_develop.get('stderr') or checkout_develop.get('message')}",
                "fetch": recovery["fetch"],
                "auto_integrated": recovery["auto_integrated"],
            }

        checkout_branch = _run(["git", "checkout", "-B", branch], cwd=repo_root, tool_context=tool_context)
        if checkout_branch.get("status") == "error":
            return {
                "status": "error",
                "message": f"Could not create branch '{branch}': {checkout_branch.get('stderr') or checkout_branch.get('message')}",
            }

        push_res = git_push(branch=branch, commit_message=f"chore: sprint {sprint_number} backlog", tool_context=tool_context)
        if push_res.get("status") != "ok":
            return {"status": "error", "message": "Failed to push the sprint backlog branch.", "push": push_res}
        actual_branch = push_res.get("branch", branch)

        pr_title = f"Sprint Backlog #{sprint_number}" + (f": {title}" if title else "")
        pr_res = gh_pr_create(
            title=pr_title,
            body=body or f"Sprint {sprint_number} planning output - roadmap, backlog, and stories reaching Ready this sprint.",
            base=develop,
            head=actual_branch,
            head_is_resolved=True,
            tool_context=tool_context,
        )
        if pr_res.get("status") != "ok":
            return {"status": "error", "message": "Failed to open the Sprint Backlog PR.", "push": push_res, "pr": pr_res}

    # "Approve sprint planning" (Stakeholder level) / budget approval (CEO):
    # this PR is now real approval evidence, not an instant self-merge - it
    # stays open, unmerged, until a fresh approval of whatever type this
    # interaction level requires before Implemented has been recorded (see
    # required_pre_implementation_approval, agents/scrum_team/helpers.py).
    # Reuses that existing approval type rather than inventing a new one:
    # the same approval that unlocks Implemented also unlocks this merge.
    required_approval = required_pre_implementation_approval()
    if required_approval:
        approvals = sum(1 for a in state.get("human_approvals", []) if a.get("type") == required_approval)
        if approvals <= state.get("sprint_approval_baseline", 0):
            return {
                "status": "ok",
                "merged": False,
                "sprint_number": sprint_number,
                "branch": actual_branch,
                "push": push_res,
                "pr": pr_res,
                "message": (
                    f"Sprint Backlog #{sprint_number} PR is open on '{actual_branch}' but not merged - "
                    f"this interaction level requires a fresh '{required_approval}' human approval "
                    f"before sprint planning can be approved. Call record_human_approval("
                    f"'{required_approval}', ...) then call create_sprint_backlog_pr() again to merge it."
                ),
            }

    # No auto-merge sweeps a develop-targeted PR the way run_eval.py's
    # _merge_open_prs does for the main-targeted release/eval-report PRs
    # (it deliberately only merges PRs against the run's main branch, same
    # reason story PRs need QA's own explicit merge_story_pr) - merge here
    # directly instead of leaving it to ride on something that won't come.
    # --admin bypasses required-review/status-check protection on develop,
    # matching how the harness already force-merges its own sprint-level
    # release PR - this PR is planning documentation, not code, so there's
    # no build/test gate meaningful to wait on.
    merge_res = _run(["gh", "pr", "merge", actual_branch, "--merge", "--admin"], cwd=repo_root, tool_context=tool_context)
    if merge_res.get("status") == "ok" and tool_context and getattr(tool_context, "state", None):
        # Only set once the merge actually succeeded - this is exactly what
        # sprint_backlog_pr_missing (agents/scrum_team/helpers.py) checks
        # before letting Dev Team start any story this sprint.
        tool_context.state["sprint_backlog_pr_sprint"] = sprint_number
        from .scrum import save_state_to_repo
        save_state_to_repo(tool_context)

    return {
        "status": "ok" if merge_res.get("status") == "ok" else "error",
        "merged": merge_res.get("status") == "ok",
        "sprint_number": sprint_number,
        "branch": actual_branch,
        "push": push_res,
        "pr": pr_res,
        "merge": merge_res,
    }

def create_story_spec_pr(title_or_id: str, tool_context=None) -> Dict[str, Any]:
    """
    GitFlow: opens a PR containing just ONE story's own spec file, distinct
    from create_sprint_backlog_pr above (which bundles every story reaching
    Ready this sprint into one PR) - a real eval run's feedback was that
    bundling gives a reviewer no way to approve one story's spec without
    approving the whole sprint's; this is the "review every story"/
    "stakeholder approval by merge request" mechanism for that.

    - At EVAL/Product/CEO (no separate human spec-reviewer at these levels -
      see docs/INTERACTION-LEVELS.md's existing rationale for why design
      approval itself isn't required at those levels either): merges
      immediately after opening, same as create_sprint_backlog_pr's
      no-approval-required path.
    - At Stakeholder: opens the PR and leaves it unmerged for the human to
      review/merge - record_design_approval now requires evidence (a real
      merge of THIS PR) instead of a bare assertion, at that level.
    """
    state = tool_context.state if tool_context and getattr(tool_context, "state", None) else {}
    product_backlog = state.get("product_backlog", []) or []
    sprint_backlog = state.get("sprint_backlog", []) or []
    match = next(
        (x for x in list(product_backlog) + list(sprint_backlog)
         if x.get("id") == title_or_id or x.get("title") == title_or_id),
        None,
    )
    if not match:
        return {"status": "error", "message": f"No story found matching '{title_or_id}'."}
    story_id = match.get("id") or title_or_id
    title = match.get("title") or story_id

    repo_root_path = _configured_repo_root(tool_context)
    repo_root = str(repo_root_path)
    is_issue = match.get("type") == "Issue"
    spec_dir = repo_root_path / "specs" / ("requirements" if is_issue else "stories")
    candidates = sorted(spec_dir.glob(f"{story_id}-*.md")) if spec_dir.exists() else []
    if not candidates:
        return {
            "status": "error",
            "message": f"No spec file found for '{story_id}' under {spec_dir} - call upsert_story/upsert_issue first.",
        }
    rel_path = str(candidates[0].relative_to(repo_root_path))

    develop = _develop_branch_name(tool_context)
    branch = f"story-spec/{story_id}"

    recovery = _checkout_develop_or_recover(repo_root, develop, tool_context=tool_context)
    checkout_develop = recovery["checkout"]
    if checkout_develop.get("status") == "error":
        return {
            "status": "error",
            "message": f"Could not check out '{develop}': {checkout_develop.get('stderr') or checkout_develop.get('message')}",
            "fetch": recovery["fetch"],
            "auto_integrated": recovery["auto_integrated"],
        }
    checkout_branch = _run(["git", "checkout", "-B", branch], cwd=repo_root, tool_context=tool_context)
    if checkout_branch.get("status") == "error":
        return {
            "status": "error",
            "message": f"Could not create branch '{branch}': {checkout_branch.get('stderr') or checkout_branch.get('message')}",
        }

    add_res = _run(["git", "add", rel_path], cwd=repo_root, tool_context=tool_context)
    # add_all=False: stage only this one story's spec file (added just
    # above), not every other pending write in the working tree - a
    # per-story spec PR is meant to be reviewable on its own, not a smaller
    # copy of the sprint backlog PR.
    push_res = git_push(branch=branch, commit_message=f"docs: {story_id} spec", add_all=False, tool_context=tool_context)
    if push_res.get("status") != "ok":
        return {"status": "error", "message": "Failed to push the story spec branch.", "add": add_res, "push": push_res}
    actual_branch = push_res.get("branch", branch)

    pr_res = gh_pr_create(
        title=f"Story Spec: {story_id} - {title}",
        body=f"Spec for {story_id}, for review/approval before it can be marked Ready.",
        base=develop,
        head=actual_branch,
        head_is_resolved=True,
        tool_context=tool_context,
    )
    if pr_res.get("status") != "ok":
        return {"status": "error", "message": "Failed to open the story spec PR.", "push": push_res, "pr": pr_res}

    merge_res = None
    merged = False
    if get_interaction_level() != "Stakeholder":
        merge_res = _run(["gh", "pr", "merge", actual_branch, "--merge", "--admin"], cwd=repo_root, tool_context=tool_context)
        merged = merge_res.get("status") == "ok"

    return {
        "status": "ok",
        "story_id": story_id,
        "branch": actual_branch,
        "push": push_res,
        "pr": pr_res,
        "merge": merge_res,
        "merged": merged,
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
    _default_push_branch). Sprint *code* already landed on develop via
    individually merged feature-branch PRs (start_feature_branch/
    merge_story_pr) by this point - but the sprint report and transcript
    have not: create_sprint_report/_write_conversation_transcript
    (tools/budget.py) only ever write specs/reports/*.md locally, onto
    whatever branch the working tree happened to be on at that moment
    (typically still whatever feature branch DevTeam last worked on -
    nothing in the normal SPRINT CLOSE SEQUENCE returns to develop
    first), and nothing else commits them either. A real incident: a
    sprint that finished entirely within budget still shipped a release
    PR with no sprint report or transcript in it at all - the budget-
    exhaustion safety net (_ensure_sprint_report_on_final_halt, agent.py)
    only covers the "ran out of budget" case, not this one, which is the
    normal/common path. This lands both on develop first, re-rendered
    directly from session state (not "carried over" from whatever's
    sitting in the working tree, which may still belong to an already-
    merged, unrelated branch), together with any other dangling specs/
    write from this sprint (a final update_roadmap/upsert_story/upsert_issue/
    generate_workflow_diagram call, via integrate_open_changes) - see the
    checkout+render+integrate+push block below.
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

    # Land the sprint report/transcript on develop before opening the PR -
    # see this function's own docstring above for why neither is there
    # yet by default. Skipped only if create_sprint_report was never
    # called at all this sprint (nothing to land) - a separate,
    # pre-existing gap this function isn't responsible for.
    if state.get("sprint_report"):
        checkout_res = _checkout_develop_or_recover(repo_root, develop, tool_context=tool_context)
        if checkout_res["status"] != "ok":
            checkout_err = checkout_res["checkout"]
            return {
                "status": "error",
                "message": (
                    f"Could not check out '{develop}' to land the sprint report/transcript before "
                    f"the release PR: {checkout_err.get('stderr') or checkout_err.get('message')}"
                ),
            }
        from .budget import render_fallback_sprint_report, _write_conversation_transcript
        render_fallback_sprint_report(tool_context=tool_context)
        _write_conversation_transcript(tool_context=tool_context)
        # integrate_open_changes rather than add_all=True: scoped to
        # specs/ and .hc/ only (see its own docstring) - by this point in
        # the sprint any *code* change should already be merged in via a
        # feature-branch PR, so the only things legitimately still
        # dangling are planning-doc writes (the report/transcript above,
        # plus whatever else update_roadmap/upsert_story/upsert_issue/
        # generate_workflow_diagram left uncommitted this sprint) - never
        # a stray build artifact or leftover file swept in by "-A".
        integrate_open_changes(tool_context=tool_context)
        land_res = _git_push_impl(
            branch=develop,
            commit_message="docs: include sprint report/transcript in release",
            add_all=False,
            allow_protected=True,
            tool_context=tool_context,
        )
        if land_res.get("status") != "ok":
            return {
                "status": "error",
                "message": "Failed to land the sprint report/transcript on develop before opening the release PR.",
                "push": land_res,
            }

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