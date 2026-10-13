# agents/scrum_team/tools/github.py
from __future__ import annotations
import os
import json
import re
from pathlib import Path
from typing import Any, Dict
from .base import _configured_repo_root, _run, _default_push_branch, _develop_branch_name, _with_eval_branch_prefix, _with_eval_title_prefix
from ..helpers import (
    required_pre_release_approval,
    required_pre_implementation_approval,
    sprint_backlog_pr_missing,
    ready_backlog_shortfall,
)

# GH #415: the version a newly-committed sprint story defaults to if it has
# none assigned yet - matches spec-templates/ROADMAP.md's own first real
# release section ("### v0.1 — MVP (target: YYYY-MM)"), so stories actually
# land there instead of the generic "Backlog (unplanned)" bucket forever.
# See create_sprint_backlog_pr's own mechanical roadmap sync.
DEFAULT_RELEASE_VERSION = "v0.1"

def release_pr_still_open(tool_context=None) -> bool:
    """
    True if a release PR (develop -> main, or their eval-run-resolved
    equivalents) is currently open and unmerged - the evidence start_sprint
    (tools/scrum.py) requires before planning the next increment. False on
    any lookup failure or genuinely no open PR, never raises - a fail-safe
    shape, since a lookup failure shouldn't itself block planning.

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
    # GH issue #399: a real eval run (0.1.0-run51) showed start_sprint's own
    # "clean working copy" sweep (tools/scrum.py) committing this sprint's
    # own not-yet-published planning output directly, onto whatever branch
    # happened to be checked out - bypassing create_sprint_backlog_pr's
    # reviewable PR entirely. create_sprint_backlog_pr's own "nothing to
    # integrate" check then had no way to tell "nothing was ever written"
    # apart from "it was already committed by this exact function, just not
    # right this instant" - this counter is that memory, bumped on every
    # successful integration regardless of which caller (or branch)
    # triggered it, so create_sprint_backlog_pr can compare against its own
    # baseline instead of only ever checking the live working tree.
    if tool_context and getattr(tool_context, "state", None):
        tool_context.state["planning_output_commit_count"] = tool_context.state.get("planning_output_commit_count", 0) + 1
    return {"status": "ok", "integrated": True, "files": files, "commit": commit}


def _checkout_with_auto_integrate(checkout_cmd: list, repo_root: str, tool_context=None) -> tuple:
    """
    Runs a git checkout command; on the specific "local changes would be
    overwritten" failure, self-heals by calling integrate_open_changes()
    (committing dangling specs/.hc writes out of the way onto whatever
    branch is currently checked out) and retrying the checkout once. Any
    other failure (network, auth, a real content conflict) is returned
    as-is, unretried - this only ever recovers the one specific, safe-to-
    auto-resolve failure mode _checkout_develop_or_recover's own docstring
    already documents in depth.

    Shared by every checkout call site in this module (GH eval run41:
    start_feature_branch's own feature-branch checkout hit exactly this and
    had no self-heal at all, unlike _checkout_develop_or_recover's develop
    checkout right next to it - DevTeam had to notice the failure and call
    integrate_open_changes itself before retrying by hand) - see
    _checkout_develop_or_recover for the original of this exact pattern,
    now factored out here so a new checkout call site can't reintroduce the
    same gap by simply forgetting to copy it.

    Returns (checkout_result, auto_integrated_result_or_None) - callers
    should surface auto_integrated in their own error message when present,
    same convention _checkout_develop_or_recover already established.
    """
    checkout = _run(checkout_cmd, cwd=repo_root, tool_context=tool_context)
    auto_integrated = None
    if checkout.get("status") == "error" and "would be overwritten" in (checkout.get("stderr") or "").lower():
        auto_integrated = integrate_open_changes(tool_context)
        if auto_integrated.get("integrated"):
            checkout = _run(checkout_cmd, cwd=repo_root, tool_context=tool_context)
    return checkout, auto_integrated


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
        checkout_cmd = ["git", "checkout", develop]
    else:
        checkout_cmd = ["git", "checkout", "-B", develop, f"origin/{develop}"]
    checkout, auto_integrated = _checkout_with_auto_integrate(checkout_cmd, repo_root, tool_context=tool_context)
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
    result = _git_push_impl(branch, commit_message, add_all, allow_protected=False, tool_context=tool_context)
    # GH issue #380: only a push to a story's own feature branch can change
    # what gh_pr_checks is actually reporting on - a sprint-backlog-PR push
    # (create_sprint_backlog_pr) touches a completely different branch and
    # shouldn't make an already-fresh CI result for an unrelated story look
    # stale. See last_pr_checks/git_push_count in state.py for the freshness
    # check this feeds (advance_story_stage's Implemented-stage gate).
    if result.get("status") == "ok" and result.get("branch", branch).startswith("feature/") \
            and tool_context and getattr(tool_context, "state", None):
        tool_context.state["git_push_count"] = tool_context.state.get("git_push_count", 0) + 1
    return result


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
    if allow_protected and branch in protected_branches:
        # GH issue #317: this call can sit a while after a caller's own last
        # _checkout_develop_or_recover sync (e.g. create_release_pr's land-
        # the-report step, with LLM-driven report rendering running in
        # between) - origin can move in that window (a concurrent
        # merge_story_pr landing a commit server-side is the common case).
        # Re-sync here, immediately before committing, with the same safe
        # fetch+preserve-then-reset pattern _checkout_develop_or_recover
        # already uses, instead of blindly committing on top of whatever
        # the working tree happens to be - a plain `checkout -B branch`
        # here (no origin sync at all) was the actual gap: local commits
        # piled up against a stale base while every push kept getting
        # rejected non-fast-forward, growing worse every retry.
        _run(["git", "fetch", "origin", branch], cwd=repo_root, tool_context=tool_context)
        preserved = _preserve_local_only_develop_commits(repo_root, branch, tool_context=tool_context)
        if preserved is not None and preserved.get("in_sync"):
            checkout_cmd = ["git", "checkout", branch]
        else:
            checkout_cmd = ["git", "checkout", "-B", branch, f"origin/{branch}"]
    else:
        # GH eval run39: a feature branch has the exact same origin-
        # divergence risk #317 fixed for protected branches - it can pick
        # up local commits across sprint/session boundaries (a fresh
        # working-copy state that's lost track of what was already pushed
        # for this same branch earlier) that a plain `checkout -B branch`
        # (no origin sync at all) never reconciles against origin's own
        # copy. A real eval run saw one feature branch diverge by 26
        # commits this way, and every subsequent push kept getting
        # rejected non-fast-forward. Only applies when origin actually has
        # a copy of this branch already - a brand-new feature branch has
        # nothing to sync against yet, so falls through to the plain
        # checkout unchanged.
        _run(["git", "fetch", "origin", branch], cwd=repo_root, tool_context=tool_context)
        remote_exists = _run(
            ["git", "rev-parse", "--verify", "--quiet", f"origin/{branch}"], cwd=repo_root, tool_context=tool_context
        ).get("returncode") == 0
        if remote_exists:
            preserved = _preserve_local_only_develop_commits(repo_root, branch, tool_context=tool_context)
            if preserved is not None and preserved.get("in_sync"):
                checkout_cmd = ["git", "checkout", branch]
            else:
                checkout_cmd = ["git", "checkout", "-B", branch, f"origin/{branch}"]
        else:
            checkout_cmd = ["git", "checkout", "-B", branch]
    # GH issue (eval run41): whichever checkout was chosen above, a dangling
    # uncommitted write under specs/.hc (from a prior story's advance_story_
    # stage roadmap update, upsert_story, etc. - none of those commit, see
    # integrate_open_changes' own docstring) can make it fail outright with
    # "local changes would be overwritten" - a real eval run hit exactly
    # this on start_feature_branch's own equivalent checkout. Self-heal via
    # the same commit-out-of-the-way-then-retry-once pattern
    # _checkout_develop_or_recover already uses for its own checkout.
    checkout, auto_integrated = _checkout_with_auto_integrate(checkout_cmd, repo_root, tool_context=tool_context)
    if checkout.get("status") == "error":
        return {
            "status": "error",
            "message": f"Could not check out branch '{branch}': {checkout.get('stderr') or checkout.get('message')}",
            "steps": {"checkout": checkout, "auto_integrated": auto_integrated},
        }

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

    # GH issue #358: advance_story_stage already refuses to let a story
    # reach Implemented-onward before the higher-priority story immediately
    # ahead of it (in product_backlog order, skipping BLOCKED predecessors)
    # has reached Accepted - but nothing stopped the real WORK (this call)
    # from starting on a lower-priority story first, wasting effort if the
    # higher-priority one later needs rework or gets blocked. Mirrors that
    # same gate here, one step earlier, at the point work actually begins.
    from .requirements import _preceding_story, _story_stages_completed, NOT_IN_PRODUCT_BACKLOG
    product_backlog = state.get("product_backlog", []) or []
    preceding = _preceding_story(product_backlog, story_id, story_id)
    if preceding is NOT_IN_PRODUCT_BACKLOG:
        return {
            "status": "error",
            "message": (
                f"Cannot start work on '{story_id}' - it isn't in product_backlog, so its priority "
                "order relative to other stories can't be verified. Add it via "
                "plan_backlog_item/upsert_story first."
            ),
        }

    # GH issue #378: advance_story_stage only ever records stage progress
    # into sprint_backlog if the story was already planned into it via
    # plan_sprint_backlog_item - a real run (0.1.0-run47) never called that
    # tool a single time across 5 whole sprints, so sprint_backlog stayed []
    # all run, every "Stories Planned" count read 0, and the KPIs sourced
    # from it (now fixed to read product_backlog instead, see
    # run_eval_analysis.py's _kpi_time_series) had nothing to show for real
    # work that was genuinely happening. Planning the story into the sprint
    # is also what gives this sprint's own backlog a record of estimate vs.
    # actual effort (log_story_tokens) - skipping it isn't just a reporting
    # gap, it's work nobody ever actually committed the sprint to.
    sprint_backlog = state.get("sprint_backlog", []) or []
    if not any(item.get("id") == story_id or item.get("title") == story_id for item in sprint_backlog):
        return {
            "status": "error",
            "message": (
                f"Cannot start work on '{story_id}' - it hasn't been planned into this sprint yet. "
                f"Call plan_sprint_backlog_item('{story_id}', plan) first (estimate, approach, etc.) "
                "so the sprint backlog actually reflects what the team committed to, then retry."
            ),
        }

    if preceding is not None and "Accepted" not in _story_stages_completed(preceding, {}):
        preceding_ref = preceding.get('id') or preceding.get('title')
        # GH issue #368: when the blocker is an Issue (an auto-filed retro/
        # impediment finding, not a feature story), resolving it for real is
        # the lead instruction, not reprioritizing it away - see
        # _preceding_story's own docstring for why leading with "just
        # deprioritize it" would turn this gate into a convenient dodge.
        escape_hatch = (
            f" '{preceding_ref}' is an auto-filed process finding - resolve it for real (advance it "
            "through Implemented -> Reviewed -> Tested -> Accepted like any other item) rather than "
            f"reaching for set_priority('{preceding_ref}', ...) to dodge it. Only reprioritize away "
            "from 'Must' if, on genuine reflection, it turns out this finding never actually warranted "
            "blocking priority - not because resolving it is inconvenient right now."
        ) if preceding.get("type") == "Issue" else ""
        return {
            "status": "error",
            "message": (
                f"Cannot start work on '{story_id}' - the higher-priority story "
                f"'{preceding_ref}' must reach Accepted first. "
                "Development happens one story at a time, top to bottom, in backlog priority order." + escape_hatch
            ),
        }

    # GH issue #359: a story cannot be resolved in reasonable effort gets
    # marked BLOCKED with a specific reason (raise_story_blocker) - work on
    # it should stop there until the reason is actually resolved
    # (resolve_story_blocker), not continue regardless. advance_story_stage
    # already refuses every further stage transition while `blocked` is
    # set; nothing previously stopped the real work (this call) from
    # starting/continuing on it anyway.
    for item in product_backlog:
        if (item.get("id") == story_id or item.get("title") == story_id) and item.get("blocked"):
            blocked = item["blocked"]
            return {
                "status": "error",
                "message": (
                    f"Cannot start work on '{story_id}' - it is BLOCKED ({blocked.get('category')}): "
                    f"{blocked.get('question')}. Call resolve_story_blocker('{story_id}', resolution) "
                    "once this has been answered, then retry."
                ),
            }

    # GH issue #357: the Sprint Backlog PR merging isn't by itself proof the
    # team actually weighed in on it - Product Owner proposes the priority/
    # sequencing, but Architect/DevTeam/QA each need to have left real
    # feedback (gh_pr_comment/gh_pr_review) on it before implementation
    # starts, not just rubber-stamp it by starting work. Reuses the same
    # pr_review_calls counter the Reviewed/Tested stage gates already rely
    # on, compared against the baseline snapshotted when this sprint's
    # backlog PR first merged (see create_sprint_backlog_pr above).
    pr_calls = state.get("pr_review_calls", {}) or {}
    engagement_baseline = state.get("sprint_backlog_engagement_baseline", {}) or {}
    missing_roles = [
        role for role in ("Architect", "DevTeam", "QA")
        if pr_calls.get(role, 0) <= engagement_baseline.get(role, 0)
    ]
    if missing_roles:
        plural = len(missing_roles) > 1
        return {
            "status": "error",
            "message": (
                f"Cannot start work on '{story_id}' - {', '.join(missing_roles)} "
                f"{'have' if plural else 'has'} not yet left a gh_pr_comment/gh_pr_review on this "
                "sprint's Sprint Backlog PR. The team gives feedback and commits to the backlog "
                "Product Owner proposed before implementation begins - have each missing role leave "
                "real feedback (even an explicit sign-off) on it first."
            ),
        }

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

    # GH issue (eval run41): this checkout has the exact same "dangling
    # uncommitted specs/.hc write" exposure as the develop checkout right
    # above it - a real eval run hit this here specifically (a previous
    # story's advance_story_stage roadmap update was still uncommitted when
    # DevTeam tried to branch off for the next one) and had no self-heal at
    # all, unlike the develop checkout next to it. Same helper, same fix.
    checkout_feature, feature_auto_integrated = _checkout_with_auto_integrate(
        ["git", "checkout", "-B", branch], repo_root, tool_context=tool_context
    )
    if checkout_feature.get("status") == "error":
        return {
            "status": "error",
            "message": f"Could not create branch '{branch}': {checkout_feature.get('stderr') or checkout_feature.get('message')}",
            "auto_integrated": feature_auto_integrated,
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

    # GH issue (0.1.0-run42): _file_retro_items_as_issues (budget.py) files
    # every retro action/impediment as a real, Must-priority Issue in
    # product_backlog specifically "so it can't be silently starved" - but
    # nothing mechanically forced Sprint Planning to ever pick one back up.
    # A real eval run showed exactly that: a dependency-pinning Issue filed
    # in Sprint 1 sat at Draft, unaddressed, through Sprints 2-5 - every
    # later sprint's own retro just filed MORE Issues on top, none of them
    # ever planned in either. This is the same choke point as the Ready-
    # backlog shortfall check right below (Dev Team can't start until this
    # PR merges), extended so a filed Must Issue can't be silently ignored
    # forever the way a merely-thin backlog already couldn't be.
    unaddressed_must_issues = [
        item for item in (state.get("product_backlog", []) or [])
        if item.get("type") == "Issue"
        and item.get("priority") == "Must"
        and not item.get("blocked")
        and "Ready" not in (item.get("stages_completed") or [])
        and "Accepted" not in (item.get("stages_completed") or [])
    ]
    if unaddressed_must_issues:
        ids = ", ".join(sorted(i.get("id") or i.get("title") or "(no id)" for i in unaddressed_must_issues))
        return {
            "status": "error",
            "message": (
                f"Cannot create the Sprint Backlog PR yet - {len(unaddressed_must_issues)} Must-priority "
                f"Issue(s) filed from a previous sprint's retrospective/impediment log are still sitting "
                f"unaddressed at Draft, never planned into a sprint: {ids}. Call "
                "advance_story_stage(id_or_title, 'Ready') for each one (then plan_sprint_backlog_item "
                "to actually queue it for this sprint), or set_priority(id_or_title, ...) to something "
                "other than 'Must' if it turns out not to actually warrant that, before retrying."
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

        checkout_branch, branch_auto_integrated = _checkout_with_auto_integrate(
            ["git", "checkout", "-B", branch], repo_root, tool_context=tool_context
        )
        if checkout_branch.get("status") == "error":
            return {
                "status": "error",
                "message": f"Could not create branch '{branch}': {checkout_branch.get('stderr') or checkout_branch.get('message')}",
                "auto_integrated": branch_auto_integrated,
            }

        # GH issue #379: this used to call git_push with its default
        # add_all=True ("git add -A"), which stages every pending write in
        # the shared checkout, not just this sprint's own planning output -
        # whichever PR-creating tool's commit lands FIRST wins everyone
        # else's not-yet-committed writes too. A real run (0.1.0-run47)
        # showed this exact race: upsert_story had already written
        # US-0003/4/5's story files to disk when this ran, swept them into
        # the sprint-backlog commit via "-A", and merged them into develop -
        # so create_story_spec_pr's own later, deliberately-scoped `git add
        # <rel_path>` found nothing left to stage and opened an empty PR.
        # integrate_open_changes is scoped to specs/+.hc/ only (same
        # pattern create_release_pr already uses for exactly this reason)
        # - this tool is only ever responsible for planning docs, never a
        # feature branch's code.
        integrate_res = integrate_open_changes(tool_context=tool_context)
        if not integrate_res.get("integrated"):
            # GH issue #399: a real eval run (0.1.0-run51) showed
            # start_sprint's own "clean working copy" sweep (tools/scrum.py)
            # commit this sprint's own not-yet-published planning output
            # directly onto whatever branch was checked out, BEFORE this
            # call ever ran - bypassing this PR's review entirely. By the
            # time this integrate_open_changes call above runs, there's
            # genuinely nothing left dirty, indistinguishable at a glance
            # from "nothing was ever written this sprint" - the exact
            # rejection below. planning_output_commit_count (bumped by every
            # successful integrate_open_changes call, github.py, regardless
            # of which caller or branch triggered it) is the memory that
            # tells the two apart: if it's moved since this PR's own last
            # successful publish, SOMETHING landed - just not reviewably.
            content_already_landed = (
                tool_context.state.get("planning_output_commit_count", 0)
                > state.get("sprint_backlog_pr_content_baseline", 0)
                if tool_context and getattr(tool_context, "state", None) else False
            )
            if not content_already_landed:
                # ISSUE-0050/0.1.0-run34: git_push's own --allow-empty fallback
                # exists so a DIFFERENT caller (create_release_pr, after
                # integrate_open_changes already committed everything itself)
                # doesn't hard-fail on "nothing staged" - reusing that same
                # fallback here would instead silently open/merge a content-free
                # "Sprint Backlog" PR, which is exactly what a real run showed
                # (nothing new planned yet, but the PR still opened and merged
                # with zero roadmap/story edits in it). This tool is the one
                # place that distinction matters, so it checks for itself
                # instead of leaning on git_push's generic behavior.
                return {
                    "status": "error",
                    "message": (
                        "Cannot create the Sprint Backlog PR - there's no new planning output (roadmap/"
                        "PRD/epics/stories) to publish this sprint yet. Write some via upsert_prd/"
                        "upsert_epic/upsert_story/update_roadmap first, then retry."
                    ),
                }
            # Content already landed on develop directly (this sprint's own
            # branch, just created from origin/develop's current tip, so it
            # already includes that commit - there's nothing left to push or
            # open a PR for; a PR comparing identical branches would be
            # empty/refused by GitHub anyway). Recognize the sprint backlog
            # as effectively published rather than rejecting forever with no
            # way for Product Owner to ever satisfy this gate again - but say
            # so plainly, since this did skip the normal review step.
            tool_context.state["sprint_backlog_pr_content_baseline"] = tool_context.state.get("planning_output_commit_count", 0)
            if tool_context.state.get("sprint_backlog_pr_sprint") != sprint_number:
                tool_context.state["sprint_backlog_engagement_baseline"] = dict(
                    tool_context.state.get("pr_review_calls", {}) or {}
                )
            tool_context.state["sprint_backlog_pr_sprint"] = sprint_number
            from .scrum import save_state_to_repo
            save_state_to_repo(tool_context)
            return {
                "status": "ok",
                "merged": True,
                "sprint_number": sprint_number,
                "warning": (
                    "This sprint's planning output (roadmap/PRD/epics/stories) was already committed "
                    "directly to develop by an earlier step (most likely start_sprint's own cleanup "
                    "sweep), before this call ever ran - there was nothing left to open a reviewable "
                    "Sprint Backlog PR for. Treating it as published so the sprint isn't permanently "
                    "stuck, but note this means it went to develop without the usual team review."
                ),
            }
        push_res = git_push(branch=branch, commit_message=f"chore: sprint {sprint_number} backlog", add_all=False, tool_context=tool_context)
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

    # "Approve sprint planning" (Product level) / budget approval (CEO):
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
    merged = merge_res.get("status") == "ok"
    if merged and tool_context and getattr(tool_context, "state", None):
        # Only set once the merge actually succeeded - this is exactly what
        # sprint_backlog_pr_missing (agents/scrum_team/helpers.py) checks
        # before letting Dev Team start any story this sprint.
        if tool_context.state.get("sprint_backlog_pr_sprint") != sprint_number:
            # GH issue #357: snapshot pr_review_calls the first time THIS
            # sprint's Sprint Backlog PR merges. start_feature_branch then
            # requires each of Architect/DevTeam/QA's count to grow past
            # this baseline before implementation can begin - at this point
            # in the sprint, this PR is the only one that could possibly
            # exist yet, so a role's gh_pr_comment/gh_pr_review call is
            # necessarily real engagement with it.
            tool_context.state["sprint_backlog_engagement_baseline"] = dict(
                tool_context.state.get("pr_review_calls", {}) or {}
            )
        tool_context.state["sprint_backlog_pr_sprint"] = sprint_number
        # GH issue #399: snapshot the same "has anything landed under specs/
        # since I last published" counter the early-return shortcut above
        # checks, so a later sprint's own content-already-landed detection
        # compares against THIS publish, not a stale one from sprints ago.
        tool_context.state["sprint_backlog_pr_content_baseline"] = tool_context.state.get("planning_output_commit_count", 0)

        # GH #415: mechanically keep specs/ROADMAP.md's release goals and
        # Kanban version-grouping populated - previously entirely optional
        # (update_roadmap's own `goals` param is never supplied unless an
        # agent happens to pass it, and a story defaults to
        # version="Backlog (unplanned)" forever unless something explicitly
        # assigns a real one). A real eval run (0.1.0-run54) showed
        # ROADMAP.md's release goals and "### v0.1 Kanban" sections left
        # permanently empty despite stories actually being tracked - just
        # under the generic unplanned bucket, never grouped into a real
        # release. Every non-Epic story newly committed to THIS sprint's
        # backlog defaults to DEFAULT_RELEASE_VERSION if it has none yet,
        # and this sprint's own goal (state.sprint_goal, set by
        # start_sprint) is appended to that version's Goals - no separate
        # agent action required, same "make it mechanical" philosophy as
        # GH #413's sprint-budget reset.
        from .requirements import update_roadmap
        sprint_story_ids = {x.get("id") or x.get("title") for x in (state.get("sprint_backlog") or [])}
        product_backlog = tool_context.state.get("product_backlog", []) or []
        versions_touched = {}
        for item in product_backlog:
            story_key = item.get("id") or item.get("title")
            if story_key not in sprint_story_ids or item.get("type") == "Epic":
                continue
            if not item.get("version") or item.get("version") == "Backlog (unplanned)":
                item["version"] = DEFAULT_RELEASE_VERSION
            versions_touched.setdefault(item["version"], []).append(story_key)
        # update_roadmap's own `goals` REPLACES a version's Goals section
        # wholesale on every call (it has no "append" mode) - accumulated
        # here in state instead (deduped) and passed as the full list every
        # time, so a later sprint's own goal doesn't erase earlier sprints'
        # already-recorded ones for the same version.
        sprint_goal = tool_context.state.get("sprint_goal")
        version_goals = dict(tool_context.state.get("version_goals") or {})
        for version, story_keys in versions_touched.items():
            goals = list(version_goals.get(version, []))
            if sprint_goal and sprint_goal not in goals:
                goals.append(sprint_goal)
            version_goals[version] = goals
            update_roadmap(version, goals=goals, stories=story_keys, tool_context=tool_context)
        tool_context.state["version_goals"] = version_goals

        from .scrum import save_state_to_repo
        save_state_to_repo(tool_context)

    result = {
        "status": "ok" if merged else "error",
        "merged": merged,
        "sprint_number": sprint_number,
        "branch": actual_branch,
        "push": push_res,
        "pr": pr_res,
        "merge": merge_res,
    }
    if merged:
        # GH issue #294: advisory-only nudge if the committed backlog looks
        # clearly under-sized relative to the sprint's token budget - see
        # sprint_capacity_advisory's own docstring for why this stays a
        # suggestion, never a gate.
        from .budget import sprint_capacity_advisory
        advisory = sprint_capacity_advisory(state)
        if advisory:
            result["capacity_advisory"] = advisory
    return result

def create_story_spec_pr(title_or_id: str, tool_context=None) -> Dict[str, Any]:
    """
    GitFlow: opens a PR containing just ONE story's own spec file, distinct
    from create_sprint_backlog_pr above (which bundles every story reaching
    Ready this sprint into one PR) - a real eval run's feedback was that
    bundling gives a reviewer no way to approve one story's spec without
    approving the whole sprint's; this is the "review every story" mechanism
    for that. Merges immediately after opening, same as
    create_sprint_backlog_pr's no-approval-required path - no interaction
    level currently requires a separate per-story design-review gate (see
    docs/INTERACTION-LEVELS.md).
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
    checkout_branch, branch_auto_integrated = _checkout_with_auto_integrate(
        ["git", "checkout", "-B", branch], repo_root, tool_context=tool_context
    )
    if checkout_branch.get("status") == "error":
        return {
            "status": "error",
            "message": f"Could not create branch '{branch}': {checkout_branch.get('stderr') or checkout_branch.get('message')}",
            "auto_integrated": branch_auto_integrated,
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

    GH issue #317: `gh pr merge` is a GitHub-API-side operation - it lands
    the merge commit on origin's develop directly, independent of this
    container's own local git state. Nothing else re-syncs the local clone
    afterward, so local develop silently falls one commit behind origin the
    moment this succeeds - a standing race for whichever write to develop
    happens next (most often create_release_pr's own push, several turns
    later). Best-effort fast-forward here closes that gap at the source
    instead of relying solely on the next write's own divergence check.
    """
    repo_root = str(_configured_repo_root(tool_context))
    cmd = ["gh", "pr", "merge"]
    if pr_id:
        cmd.append(str(pr_id))
    cmd.append("--merge")
    if admin:
        cmd.append("--admin")
    result = _run(cmd, cwd=repo_root, tool_context=tool_context)
    if result.get("status") == "ok":
        develop = _develop_branch_name(tool_context)
        _run(["git", "fetch", "origin", develop], cwd=repo_root, tool_context=tool_context)
        _run(["git", "checkout", develop], cwd=repo_root, tool_context=tool_context)
        # --ff-only: never overwrite or discard local-only work here - if
        # local has diverged, this is a no-op and the next
        # _checkout_develop_or_recover call remains the authoritative
        # recovery path (it already handles that case safely).
        _run(["git", "merge", "--ff-only", f"origin/{develop}"], cwd=repo_root, tool_context=tool_context)
    return result

_CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def resolve_feature_branch_conflicts(story_id: str, tool_context=None) -> Dict[str, Any]:
    """
    GH issue #382: a real eval run showed a story's feature-branch PR go
    "not mergeable" (merge_story_pr) because it had diverged from develop -
    DevTeam was transferred to "resolve merge conflicts" 5 times in a row,
    but git_push (its only repair tool) only ever re-fetches/re-pushes the
    SAME branch; it never actually runs `git merge`/rebase against develop.
    Every retry hit the identical error, and the team eventually gave up and
    recreated the PR from scratch - only survivable because that story's
    diff happened to be small enough to cheaply redo.

    Merges the current `develop` into story_id's active feature branch
    (active_feature_branches[story_id], set by start_feature_branch) to
    resolve exactly that failure:
    - No real conflict (clean merge, or already up to date): commits
      (if anything to commit) and pushes immediately - retry merge_story_pr.
    - Real conflicts: returns each conflicted file's current on-disk content
      (with git's own <<<<<<</=======/>>>>>>> markers) so DevTeam can
      resolve them via write_file, then call this again - it detects which
      files still contain marker lines (not yet actually resolved) versus
      which are clean now, stages+commits+pushes once none remain, and
      reports the still-unresolved ones again otherwise. Safe to call
      repeatedly as resolution progresses one file at a time.
    """
    state = tool_context.state if tool_context and getattr(tool_context, "state", None) else {}
    branch = (state.get("active_feature_branches") or {}).get(story_id)
    if not branch:
        return {
            "status": "error",
            "message": (
                f"No active feature branch recorded for '{story_id}' - call start_feature_branch "
                "first, or check the story_id is correct."
            ),
        }

    repo_root = str(_configured_repo_root(tool_context))
    develop = _develop_branch_name(tool_context)

    checkout, auto_integrated = _checkout_with_auto_integrate(
        ["git", "checkout", branch], repo_root, tool_context=tool_context
    )
    if checkout.get("status") == "error":
        return {
            "status": "error",
            "message": f"Could not check out '{branch}': {checkout.get('stderr') or checkout.get('message')}",
            "auto_integrated": auto_integrated,
        }

    merge_in_progress = _run(
        ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=repo_root, tool_context=tool_context
    ).get("returncode") == 0

    if not merge_in_progress:
        _run(["git", "fetch", "origin", develop], cwd=repo_root, tool_context=tool_context)
        merge_res = _run(
            ["git", "merge", f"origin/{develop}", "--no-commit", "--no-ff"],
            cwd=repo_root, tool_context=tool_context,
        )
        if "up to date" in (merge_res.get("stdout") or "").lower() and merge_res.get("status") == "ok":
            return {
                "status": "ok",
                "merged": False,
                "message": f"'{branch}' is already up to date with '{develop}' - nothing to resolve.",
            }

    conflicted = _run(
        ["git", "diff", "--name-only", "--diff-filter=U"], cwd=repo_root, tool_context=tool_context
    )
    conflicted_paths = [p for p in (conflicted.get("stdout") or "").splitlines() if p.strip()]

    if not conflicted_paths:
        # A clean merge pending commit (--no-commit above), or a previously
        # in-progress merge whose last conflicted file was just resolved.
        _run(["git", "add", "-A"], cwd=repo_root, tool_context=tool_context)
        commit_res = _run(
            ["git", "commit", "--no-edit"], cwd=repo_root, tool_context=tool_context
        )
        if commit_res.get("status") != "ok" and "nothing to commit" not in (commit_res.get("stdout") or "").lower():
            return {"status": "error", "message": "Merge resolved but commit failed.", "commit": commit_res}
        push_res = git_push(branch=branch, commit_message=f"merge: resolve conflicts with {develop}", add_all=False, tool_context=tool_context)
        if push_res.get("status") != "ok":
            return {"status": "error", "message": "Merge resolved and committed, but push failed.", "push": push_res}
        return {
            "status": "ok",
            "merged": True,
            "message": f"Merged '{develop}' into '{branch}' and pushed - retry merge_story_pr.",
        }

    unresolved = {}
    for rel_path in conflicted_paths:
        abs_path = (Path(repo_root) / rel_path).resolve()
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            unresolved[rel_path] = f"(could not read file: {e})"
            continue
        if any(marker in content for marker in _CONFLICT_MARKERS):
            unresolved[rel_path] = content
        else:
            # Already rewritten (by a prior write_file call) with no marker
            # lines left - stage it now so it doesn't show as conflicted
            # the next time this is called.
            _run(["git", "add", "--", rel_path], cwd=repo_root, tool_context=tool_context)

    if unresolved:
        return {
            "status": "conflict",
            "conflicted_files": unresolved,
            "message": (
                f"{len(unresolved)} file(s) still have real conflict markers - resolve each via "
                "write_file (replacing the <<<<<<</=======/>>>>>>> sections with the real, correct "
                "content), then call resolve_feature_branch_conflicts again."
            ),
        }

    # Every previously-conflicted file is now staged with no markers left.
    commit_res = _run(["git", "commit", "--no-edit"], cwd=repo_root, tool_context=tool_context)
    if commit_res.get("status") != "ok":
        return {"status": "error", "message": "All conflicts resolved but commit failed.", "commit": commit_res}
    push_res = git_push(branch=branch, commit_message=f"merge: resolve conflicts with {develop}", add_all=False, tool_context=tool_context)
    if push_res.get("status") != "ok":
        return {"status": "error", "message": "Conflicts resolved and committed, but push failed.", "push": push_res}
    return {
        "status": "ok",
        "merged": True,
        "message": f"All conflicts resolved, merged '{develop}' into '{branch}', and pushed - retry merge_story_pr.",
    }


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
            result = {"status": "ok", "passing": True, "message": "No checks defined.", "details": r}
        # Pending case (exit code 8)
        elif r.get("returncode") == 8:
            result = {"status": "pending", "passing": False, "message": "Checks are pending.", "details": r}
        else:
            result = {"status": "error", "passing": False, "message": "Checks are failing or another error occurred.", "details": r}
    else:
        result = {"status": "ok", "passing": True, "message": "All checks passing.", "details": r}

    # GH issue #380: advance_story_stage's Implemented-stage gate requires a
    # gh_pr_checks() result recorded here - same "last tool result, snapshot
    # the staleness counter at call time" pattern check_build() already uses
    # (last_check_build/dependency_manifest_write_count) - before this,
    # nothing anywhere actually required CI to be checked at all before a
    # story could be marked Implemented.
    if tool_context and getattr(tool_context, "state", None):
        tool_context.state["last_pr_checks"] = {
            "passing": result.get("passing"),
            "git_push_count_at_check": tool_context.state.get("git_push_count", 0),
        }
    return result

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
        # GH issue #397: this (and create_sprint_report's own baseline
        # bumps) previously only ever landed in the live in-memory session
        # state - neither function persisted it, unlike nearly every other
        # state-mutating tool. A stale .hc/state.json left over from a
        # mid-sprint save (e.g. advance_story_stage, after an earlier
        # rejected create_sprint_report attempt) could then get reloaded by
        # a later init_scrum_state() call - it runs unconditionally
        # whenever the file exists, including at the start of every sprint
        # - clobbering the correctly-cleared sprint_report_pending_release
        # with a stale True/False mismatch and resurrecting a false
        # sprint_report_step_active() condition (helpers.py) that locked
        # Scrum Master out of every tool but transfer_to_agent, bouncing
        # with Product Owner until the loop breaker tripped (0.1.0-run50).
        if tool_context and getattr(tool_context, "state", None):
            from .scrum import save_state_to_repo
            save_state_to_repo(tool_context)
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

def gh_pr_comments(pr_id: str | int | None = None, tool_context=None) -> Dict[str, Any]:
    """
    Reads back the comments and reviews already left on a Pull Request -
    the read counterpart to gh_pr_comment/gh_pr_review, which only ever
    write. Every comment this codebase posts is prefixed with the posting
    role's own name (`**Architect:**`, `**QA:**`, ...) regardless of the
    underlying GitHub account they all share, so that prefix - not the raw
    GitHub username - is how a caller tells who said what.

    Without this, a role has no way to see what another role actually said
    on a PR - only whether the mechanical team-engagement gate (GH #357)
    considers them to have commented at all. A real eval run showed DevTeam
    unable to tell whether QA/Architect had already left feedback worth
    reading before deciding whether to repeat work or wait, and separately
    tried (and failed) to satisfy QA's own missing-engagement requirement
    by posting a comment itself worded as QA's sign-off - gh_pr_comment
    attributes by the real calling agent, never by what the text claims,
    so this is also how a role can *verify* that before assuming it worked.

    - pr_id: optional PR number, URL, or branch. If None, uses current branch.
    Returns `comments` (general PR conversation) and `reviews` (formal
    gh_pr_review submissions), each as a list of {author, body, created_at}
    in chronological order - empty lists, not an error, if none exist yet.
    """
    repo_root = str(_configured_repo_root(tool_context))
    cmd = ["gh", "pr", "view"]
    if pr_id:
        cmd.append(str(pr_id))
    cmd += ["--json", "comments,reviews"]
    r = _run(cmd, cwd=repo_root, tool_context=tool_context)
    if r.get("status") != "ok":
        return r
    try:
        data = json.loads(r.get("stdout") or "{}")
    except ValueError:
        return {"status": "error", "message": "Could not parse gh pr view output.", "details": r}
    comments = [
        {
            "author": (c.get("author") or {}).get("login"),
            "body": c.get("body"),
            "created_at": c.get("createdAt"),
        }
        for c in data.get("comments", []) or []
    ]
    reviews = [
        {
            "author": (rv.get("author") or {}).get("login"),
            "state": rv.get("state"),
            "body": rv.get("body"),
            "submitted_at": rv.get("submittedAt"),
        }
        for rv in data.get("reviews", []) or []
    ]
    return {"status": "ok", "comments": comments, "reviews": reviews}

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