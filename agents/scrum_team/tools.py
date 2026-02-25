# agents/scrum_team/tools.py
from __future__ import annotations

from typing import Any, Dict, List
import os
import json
import shutil
import subprocess
import time
from pathlib import Path

import jwt
import requests

DEFAULT_DOD = [
    "Code reviewed",
    "Automated tests passing",
    "Acceptance criteria met",
    "No critical security issues",
    "Docs updated if needed",
]

def init_scrum_state(tool_context=None) -> Dict[str, Any]:
    """
    Initialize all Scrum artifacts in session.state if missing.
    If a repo is configured and a state file exists, load it.
    """
    s = tool_context.state

    s.setdefault("product_vision", "")
    s.setdefault("product_goals", [])
    s.setdefault("product_backlog", [])          # list[dict]
    s.setdefault("definition_of_done", list(DEFAULT_DOD))
    s.setdefault("sprint_goal", "")
    s.setdefault("sprint_backlog", [])           # list[dict]
    s.setdefault("impediment_log", [])           # list[dict]
    s.setdefault("retro_actions", [])            # list[dict]
    s.setdefault("decision_log", [])             # list[dict]
    s.setdefault("sprint_report", "")
    s.setdefault("budgets", {"total": 0, "agents": {}})
    s.setdefault("token_usage", {"total": 0, "agents": {}})
    s.setdefault("story_estimates", {})

    # Try to load from repo if present
    try:
        repo_root = _configured_repo_root(tool_context)
        fp = _state_file_path(repo_root)
        if fp.exists():
            _ = load_state_from_repo(tool_context)
    except Exception:
        pass

    return {"status": "ok", "initialized": True}

def log_decision(title: str, decision: str, rationale: str, owner: str, tool_context=None) -> Dict[str, Any]:
    """
    Append a decision to decision_log.
    """
    s = tool_context.state
    entry = {
        "title": title.strip(),
        "decision": decision.strip(),
        "rationale": rationale.strip(),
        "owner": owner.strip(),
    }
    s["decision_log"] = list(s.get("decision_log", [])) + [entry]
    _ = save_state_to_repo(tool_context)
    return {"status": "ok", "decision": entry}

def upsert_backlog_item(item: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    """
    Add or update a product backlog item by id (preferred) or by title.
    """
    s = tool_context.state
    backlog: List[Dict[str, Any]] = list(s.get("product_backlog", []))

    item_id = item.get("id")
    title = item.get("title")
    if not item_id and not title:
        return {"status": "error", "message": "Backlog item needs at least 'id' or 'title'."}

    def matches(x: Dict[str, Any]) -> bool:
        return (item_id and x.get("id") == item_id) or (title and x.get("title") == title)

    for i, x in enumerate(backlog):
        if matches(x):
            backlog[i] = {**x, **item}
            s["product_backlog"] = backlog
            _ = save_state_to_repo(tool_context)
            return {"status": "ok", "updated": True, "item": backlog[i]}

    backlog.append(item)
    s["product_backlog"] = backlog
    _ = save_state_to_repo(tool_context)
    return {"status": "ok", "updated": False, "item": item}

def set_priority(title_or_id: str, priority: str, tool_context=None) -> Dict[str, Any]:
    """
    Update priority for a backlog item.
    """
    s = tool_context.state
    backlog: List[Dict[str, Any]] = list(s.get("product_backlog", []))

    for x in backlog:
        if x.get("id") == title_or_id or x.get("title") == title_or_id:
            x["priority"] = priority
            s["product_backlog"] = backlog
            _ = save_state_to_repo(tool_context)
            return {"status": "ok", "item": x}

    return {"status": "error", "message": "Item not found."}

def add_impediment(description: str, owner: str, tool_context=None) -> Dict[str, Any]:
    """
    Add an impediment to impediment_log.
    """
    s = tool_context.state
    imp = {"description": description.strip(), "owner": owner.strip(), "status": "open"}
    s["impediment_log"] = list(s.get("impediment_log", [])) + [imp]
    _ = save_state_to_repo(tool_context)
    return {"status": "ok", "impediment": imp}

def add_retro_action(action: str, owner: str, success_metric: str, tool_context=None) -> Dict[str, Any]:
    """
    Add an action item from retrospectives.
    """
    s = tool_context.state
    entry = {
        "action": action.strip(),
        "owner": owner.strip(),
        "success_metric": success_metric.strip(),
        "status": "open",
    }
    s["retro_actions"] = list(s.get("retro_actions", [])) + [entry]
    _ = save_state_to_repo(tool_context)
    return {"status": "ok", "retro_action": entry}

def plan_sprint_backlog_item(title_or_id: str, plan: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    """
    Add/update an item in sprint_backlog with implementation plan fields:
      approach: str
      tasks: list[str]
      estimate: str | number (in tokens)
      risks: list[str]
      test_approach: str
      dod_checks: list[str]
    """
    s = tool_context.state
    sprint: List[Dict[str, Any]] = list(s.get("sprint_backlog", []))

    # Also update story_estimates
    estimates = s.get("story_estimates", {})
    if "estimate" in plan:
        estimates[title_or_id] = plan["estimate"]
        s["story_estimates"] = estimates

    key = title_or_id
    for i, x in enumerate(sprint):
        if x.get("id") == key or x.get("title") == key:
            sprint[i] = {**x, **plan}
            s["sprint_backlog"] = sprint
            _ = save_state_to_repo(tool_context)
            return {"status": "ok", "updated": True, "item": sprint[i]}

    entry = {"title": key, **plan}
    sprint.append(entry)
    s["sprint_backlog"] = sprint
    _ = save_state_to_repo(tool_context)
    return {"status": "ok", "updated": False, "item": entry}

# -------------------------
# Repo selection and state persistence helpers
# -------------------------

REPO_STATE_KEYS = [
    "product_vision",
    "product_goals",
    "product_backlog",
    "definition_of_done",
    "sprint_goal",
    "sprint_backlog",
    "impediment_log",
    "retro_actions",
    "decision_log",
    "sprint_report",
    "budgets",
    "token_usage",
    "story_estimates",
]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _configured_repo_root(tool_context=None) -> Path:
    """
    Determine which repository directory to operate in.
    Preference order:
    - tool_context.state['repo']['local_path'] if present
    - current project root (fallback)
    """
    try:
        if tool_context and getattr(tool_context, "state", None):
            repo_cfg = tool_context.state.get("repo", {}) or {}
            p = repo_cfg.get("local_path")
            if p:
                return Path(p).expanduser().resolve()
    except Exception:
        pass
    return _project_root()


def _state_file_path(repo_root: Path) -> Path:
    return (repo_root / ".hc" / "state.json").resolve()


def save_state_to_repo(tool_context=None) -> Dict[str, Any]:
    """
    Persist selected scrum state keys into the configured repo under .hc/state.json.
    """
    repo_root = _configured_repo_root(tool_context)
    try:
        repo_root.mkdir(parents=True, exist_ok=True)
        state_dir = repo_root / ".hc"
        state_dir.mkdir(parents=True, exist_ok=True)
        snapshot = {k: tool_context.state.get(k) for k in REPO_STATE_KEYS}
        _state_file_path(repo_root).write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"status": "ok", "path": str(_state_file_path(repo_root))}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def load_state_from_repo(tool_context=None) -> Dict[str, Any]:
    """
    Load previously persisted scrum state from .hc/state.json into session.state (upsert/merge).
    """
    repo_root = _configured_repo_root(tool_context)
    fp = _state_file_path(repo_root)
    if not fp.exists():
        return {"status": "error", "message": f"State file not found: {fp}"}
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"status": "error", "message": "Invalid state format in state.json"}
        for k, v in data.items():
            tool_context.state[k] = v
        return {"status": "ok", "loaded_keys": list(data.keys())}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def configure_github_repo(repo_url: str, local_path: str = "", default_branch: str = "main", tool_context=None) -> Dict[str, Any]:
    """
    Configure the GitHub repository used for persistence and tooling.
    - repo_url: SSH or HTTPS URL
    - local_path: optional existing checkout or desired clone path. If empty, will use project_root/source/state_repo
    - default_branch: branch used for pushes/releases by default
    This will clone the repo if local_path does not exist.
    """
    proj_root = _project_root()
    target_dir = Path(local_path).expanduser() if local_path else (proj_root / "source" / "state_repo")
    target_dir = target_dir.resolve()
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    # If the directory is not a git repo, attempt clone
    if not (target_dir / ".git").exists():
        # Best effort: clone
        try:
            result = _run(["git", "clone", repo_url, str(target_dir)], cwd=str(proj_root))
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
    Configure and authenticate using a GitHub App installation.
    - app_id: The ID of the GitHub App
    - private_key: The content of the App's private key (.pem)
    - installation_id: The installation ID for the target repository/org
    This tool generates an installation token and stores it in session.state['github_token'].
    """
    # 1. Create a JWT for the GitHub App
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": app_id,
    }
    try:
        encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")
    except Exception as e:
        return {"status": "error", "message": f"JWT encoding failed: {e}"}

    # 2. Get an installation access token
    headers = {
        "Authorization": f"Bearer {encoded_jwt}",
        "Accept": "application/vnd.github+json",
    }
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    try:
        resp = requests.post(url, headers=headers, timeout=10)
        resp.raise_for_status()
        token_data = resp.json()
        token = token_data.get("token")
        if not token:
            return {"status": "error", "message": "Failed to retrieve token from GitHub API"}

        # Store in state
        tool_context.state["github_app"] = {
            "app_id": app_id,
            "private_key": private_key,
            "installation_id": installation_id,
            "expires_at": token_data.get("expires_at"),
        }
        tool_context.state["github_token"] = token
        return {"status": "ok", "message": "GitHub App authenticated successfully", "expires_at": token_data.get("expires_at")}
    except Exception as e:
        return {"status": "error", "message": f"Failed to get installation token: {e}"}


def seed_repository(overwrite: bool = False, tool_context=None) -> Dict[str, Any]:
    """
    Copy the docs/ directory from the current project into the configured target repo,
    and create a product-specific README.md.
    Then performs an initial commit and push.
    - overwrite: If True, existing files in the target will be replaced.
    """
    proj_root = _project_root()
    repo_root = _configured_repo_root(tool_context)
    if repo_root == proj_root:
        return {"status": "error", "message": "The configured target repository is the same as the project root. Seeding is not allowed here."}

    # Ensure target exists
    repo_root.mkdir(parents=True, exist_ok=True)
    files_seeded = []

    try:
        # Create/Update README.md
        dst_readme = repo_root / "README.md"
        if not dst_readme.exists() or overwrite:
            # Try to build a README from session.state
            vision = tool_context.state.get("product_vision", "").strip()
            goals = tool_context.state.get("product_goals", [])
            
            content = ""
            if vision:
                content = f"# Product Vision\n\n{vision}\n"
            else:
                content = "# Project README\n\nWelcome to your new project repository.\n"
            
            if goals:
                content += "\n## Product Goals\n"
                for g in goals:
                    content += f"- {g}\n"
            
            content += "\n## Documentation\nSee [docs/README.md](docs/README.md) for details on the repository structure.\n"
            
            dst_readme.write_text(content, encoding="utf-8")
            files_seeded.append("README.md")

        # Copy docs/ directory
        src_docs = proj_root / "docs"
        dst_docs = repo_root / "docs"
        if src_docs.exists():
            if not dst_docs.exists():
                shutil.copytree(src_docs, dst_docs)
                files_seeded.append("docs/")
            elif overwrite:
                # Merge docs/ or clear and copy
                shutil.rmtree(dst_docs)
                shutil.copytree(src_docs, dst_docs)
                files_seeded.append("docs/ (overwritten)")

        # Initial commit and push
        if files_seeded:
            push_res = git_push(
                branch="main", # default to main for seeding
                commit_message="chore: initial seed of README and docs",
                add_all=True,
                tool_context=tool_context
            )
            return {"status": "ok", "seeded": files_seeded, "push": push_res}

        return {"status": "ok", "message": "No new files seeded.", "seeded": []}
    except Exception as e:
        return {"status": "error", "message": f"Seeding failed: {e}"}


def repo_status(tool_context=None) -> Dict[str, Any]:
    """
    Return detected repo configuration and quick diagnostics.
    """
    cfg = (tool_context.state.get("repo") if tool_context and getattr(tool_context, "state", None) else None) or {}
    root = _configured_repo_root(tool_context)
    diagnostics = {
        "exists": root.exists(),
        "git_dir": (root / ".git").exists(),
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
        # Try gh auth status (non-fatal)
        gh = _run(["gh", "auth", "status"], cwd=str(root), tool_context=tool_context)
        diagnostics["gh_auth_ok"] = (gh.get("returncode") == 0)

    return {"status": "ok", "config": cfg, "repo_root": str(root), "diagnostics": diagnostics}


# -------------------------
# Git/GitHub integration
# -------------------------

def _run(cmd: list[str], cwd: str | None = None, tool_context=None) -> Dict[str, Any]:
    """
    Run a shell command non-interactively and capture output.
    Injects GH_TOKEN if present in session.state.
    """
    env = os.environ.copy()
    if tool_context and getattr(tool_context, "state", None):
        token = tool_context.state.get("github_token")
        if token:
            env["GH_TOKEN"] = token
            env["GITHUB_TOKEN"] = token
            # Also configure git to use the token for HTTPS
            # (only if we have a repo config with an HTTPS URL)
            repo_cfg = tool_context.state.get("repo", {})
            if repo_cfg.get("url", "").startswith("https://"):
                _ = subprocess.run(
                    ["git", "config", "http.https://github.com/.extraheader", f"AUTHORIZATION: basic {token}"],
                    cwd=cwd or str(_project_root()),
                    env=env,
                    capture_output=True
                )

    try:
        p = subprocess.run(
            cmd,
            cwd=cwd or str(_project_root()),
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        return {
            "status": "ok" if p.returncode == 0 else "error",
            "returncode": p.returncode,
            "stdout": p.stdout.strip(),
            "stderr": p.stderr.strip(),
            "cmd": cmd,
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "cmd": cmd}


def git_push(branch: str, commit_message: str = "chore: update", add_all: bool = True, tool_context=None) -> Dict[str, Any]:
    """
    Stage changes (optionally), commit, and push the current working tree to the given branch.
    Non-interactive; returns command outputs.
    """
    repo_root = str(_configured_repo_root(tool_context))

    # Ensure branch exists locally
    _ = _run(["git", "checkout", "-B", branch], cwd=repo_root)

    if add_all:
        r1 = _run(["git", "add", "-A"], cwd=repo_root)
        if r1.get("status") == "error":
            return r1
    r2 = _run(["git", "commit", "-m", commit_message], cwd=repo_root)
    # Allow empty commit to ensure branch gets pushed
    if r2.get("returncode") != 0:
        # Try creating an empty commit when nothing to commit
        if "nothing to commit" in (r2.get("stderr") or "") + (r2.get("stdout") or ""):
            r2 = _run(["git", "commit", "--allow-empty", "-m", commit_message], cwd=repo_root)
        # If still failing, continue to push in case branch update is desired
    r3 = _run(["git", "push", "-u", "origin", branch], cwd=repo_root)

    return {"status": "ok" if r3.get("status") == "ok" else "error", "steps": {"checkout": _, "add": r1 if add_all else None, "commit": r2, "push": r3}}


def gh_pr_create(title: str, body: str = "", base: str = "main", head: str | None = None, draft: bool = False, tool_context=None) -> Dict[str, Any]:
    """
    Create a Pull Request using `gh` CLI. Assumes authentication is set up.
    - base: target branch (e.g., main)
    - head: source branch (defaults to current if None)
    - draft: open PR as draft
    """
    repo_root = str(_configured_repo_root(tool_context))
    cmd = ["gh", "pr", "create", "--base", base, "--title", title]
    if body:
        cmd += ["--body", body]
    if head:
        cmd += ["--head", head]
    if draft:
        cmd += ["--draft"]
    r = _run(cmd, cwd=repo_root)
    return r


def gh_pr_status(tool_context=None) -> Dict[str, Any]:
    """
    Check the status of Pull Requests for the current repository and user/app.
    """
    repo_root = str(_configured_repo_root(tool_context))
    r = _run(["gh", "pr", "status"], cwd=repo_root)
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
    
    # If command failed, check why. 
    # gh pr checks returns non-zero if checks are failing or pending.
    # Exit code 8 means checks pending.
    
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

    # If status is "ok", it means gh command succeeded.
    # We should still parse JSON to be sure.
    try:
        checks = json.loads(r["stdout"])
        if not checks:
            return {"status": "ok", "passing": True, "message": "No checks found.", "details": r}
        
        all_passing = all(c.get("bucket") == "pass" for c in checks)
        if all_passing:
            return {"status": "ok", "passing": True, "message": "All checks passing.", "checks": checks}
        else:
            # Check if any are pending in the list
            if any(c.get("bucket") == "pending" for c in checks):
                return {"status": "pending", "passing": False, "message": "Some checks are pending.", "checks": checks}
            return {"status": "error", "passing": False, "message": "Some checks failed.", "checks": checks}
    except Exception as e:
        # Fallback if JSON parsing fails but command succeeded
        return {"status": "ok", "passing": True, "message": f"Checks command succeeded but output parsing failed: {e}", "details": r}


def gh_release_create(tag: str, title: str | None = None, notes: str | None = None, generate_notes: bool = False, draft: bool = False, prerelease: bool = False, tool_context=None) -> Dict[str, Any]:
    """
    Create a GitHub release using `gh release create`.
    - tag: version tag (e.g., v0.1.0)
    - title: optional release title
    - notes: optional release notes text
    - generate_notes: if True, let GitHub auto-generate notes
    - draft/prerelease flags supported
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
    r = _run(cmd, cwd=repo_root)
    return r

# -------------------------
# Documentation from templates
# -------------------------

def write_file(path: str, content: str, overwrite: bool = False, tool_context=None) -> Dict[str, Any]:
    """
    Write content to a repository-relative file path.
    """
    repo_root = _configured_repo_root(tool_context)
    abs_path = (repo_root / path).resolve()
    try:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        if abs_path.exists() and not overwrite:
            return {"status": "error", "message": f"File exists: {path}"}
        abs_path.write_text(content, encoding="utf-8")
        return {"status": "ok", "path": str(abs_path)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def create_from_template(template_path: str, destination_path: str, substitutions_json: str = "{}", overwrite: bool = False, tool_context=None) -> Dict[str, Any]:
    """
    Create a documentation file from a template under docs/.
    - template_path: path relative to repo root (e.g., docs/requirements/TEMPLATE-PRD.md)
    - destination_path: output file path relative to repo root
    - substitutions_json: JSON dict of placeholder -> value. Placeholders formatted as <KEY> in template.
    - overwrite: whether to overwrite existing file
    """
    repo_root = _configured_repo_root(tool_context)
    src = (repo_root / template_path).resolve()
    dst = (repo_root / destination_path).resolve()
    try:
        if not src.exists():
            return {"status": "error", "message": f"Template not found: {template_path}"}
        raw = src.read_text(encoding="utf-8")
        try:
            subs: Dict[str, Any] = json.loads(substitutions_json or "{}")
        except json.JSONDecodeError as e:
            return {"status": "error", "message": f"Invalid JSON: {e}"}
        text = raw
        for k, v in subs.items():
            text = text.replace(f"<{k}>", str(v))
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and not overwrite:
            return {"status": "error", "message": f"File exists: {destination_path}"}
        dst.write_text(text, encoding="utf-8")
        return {"status": "ok", "path": str(dst)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# -------------------------
# Sprint and Budget Management
# -------------------------

def update_budgets(total: int = None, agent_budgets: Dict[str, int] = None, tool_context=None) -> Dict[str, Any]:
    """
    Update the total and per-agent token budgets.
    """
    s = tool_context.state
    budgets = s.get("budgets", {"total": 0, "agents": {}})
    if total is not None:
        budgets["total"] = total
    if agent_budgets:
        budgets["agents"].update(agent_budgets)
    s["budgets"] = budgets
    _ = save_state_to_repo(tool_context)
    return {"status": "ok", "budgets": budgets}

def get_budget_status(tool_context=None) -> Dict[str, Any]:
    """
    Return the current budget usage and status.
    """
    s = tool_context.state
    budgets = s.get("budgets", {"total": 0, "agents": {}})
    usage = s.get("token_usage", {"total": 0, "agents": {}})
    
    status = {
        "budgets": budgets,
        "usage": usage,
        "remaining_total": budgets["total"] - usage["total"],
        "is_over_budget": usage["total"] >= budgets["total"] if budgets["total"] > 0 else False
    }
    return {"status": "ok", "budget_status": status}

def log_token_usage(agent_name: str, tokens: int, tool_context=None) -> Dict[str, Any]:
    """
    Manually log token usage for an agent (e.g. after a meeting or if automatic tracking is missing).
    """
    s = tool_context.state
    usage = s.get("token_usage", {"total": 0, "agents": {}})
    
    usage["total"] += tokens
    usage["agents"][agent_name] = usage["agents"].get(agent_name, 0) + tokens
    
    s["token_usage"] = usage
    _ = save_state_to_repo(tool_context)
    return {"status": "ok", "usage": usage}

def create_sprint_report(summary: str, accomplishments: List[str], tool_context=None) -> Dict[str, Any]:
    """
    Create a management summary report as the sprint review.
    Persists it to session.state and saves it to a file in the repo.
    """
    s = tool_context.state
    usage = s.get("token_usage", {"total": 0, "agents": {}})
    retro_actions = s.get("retro_actions", [])
    
    report = f"# Sprint Review Report\n\n## Summary\n{summary}\n\n## Accomplishments\n"
    for a in accomplishments:
        report += f"- {a}\n"
    
    report += f"\n## Token Usage\n- Total: {usage['total']}\n"
    for agent, agent_usage in usage.get("agents", {}).items():
        report += f"  - {agent}: {agent_usage}\n"
    
    report += "\n## Retrospective Actions (including efficiency improvements)\n"
    for action in retro_actions:
        report += f"- {action.get('action')} (Owner: {action.get('owner')}, Status: {action.get('status')})\n"

    estimates = s.get("story_estimates", {})
    if estimates:
        report += "\n## Story Estimates (Tokens)\n"
        for story_id, estimate in estimates.items():
            report += f"- {story_id}: {estimate}\n"

    s["sprint_report"] = report
    _ = save_state_to_repo(tool_context)
    
    # Also write to repo
    res = write_file("docs/reports/SPRINT-REPORT-LATEST.md", report, overwrite=True, tool_context=tool_context)
    
    return {"status": "ok", "report": report, "file_save": res}

def create_release_pr(title: str, body: str, branch: str = "release/increment", tool_context=None) -> Dict[str, Any]:
    """
    Create a pull request for the increment of the release, containing all changes.
    """
    # First, push current changes to the release branch
    push_res = git_push(branch=branch, commit_message=f"release: {title}", add_all=True, tool_context=tool_context)
    if push_res.get("status") == "error":
        return push_res
    
    # Then create the PR
    pr_res = gh_pr_create(title=title, body=body, base="main", head=branch, tool_context=tool_context)
    return {"status": "ok", "push": push_res, "pr": pr_res}