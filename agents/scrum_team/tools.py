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

    # Initialize defaults
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

    # 1. Load from environment variables (overrides defaults, but not repo state)
    env_token_budget = os.environ.get("SPRINT_TOKEN_BUDGET")
    if env_token_budget:
        try:
            s["budgets"]["total"] = int(env_token_budget)
        except ValueError:
            pass
    
    env_usd_budget = os.environ.get("SPRINT_USD_BUDGET")
    if env_usd_budget:
        try:
            s["budgets"]["total_usd"] = float(env_usd_budget)
        except ValueError:
            pass

    env_repo_url = os.environ.get("GITHUB_REPO_URL")
    if env_repo_url:
        repo_cfg = s.get("repo", {}) or {}
        repo_cfg.setdefault("url", env_repo_url)
        repo_cfg.setdefault("local_path", os.environ.get("GITHUB_REPO_LOCAL_PATH", ""))
        repo_cfg.setdefault("default_branch", os.environ.get("GITHUB_REPO_BRANCH", "main"))
        s["repo"] = repo_cfg

    # 1.1 Load GitHub App credentials from environment
    app_id = os.environ.get("GITHUB_APP_ID")
    private_key = os.environ.get("GITHUB_APP_PRIVATE_KEY")
    inst_id = os.environ.get("GITHUB_APP_INSTALLATION_ID")
    
    if app_id and private_key and inst_id:
        # Check if already configured to avoid redundant token requests
        app_cfg = s.get("github_app", {})
        
        # Normalize the key for comparison
        clean_key = _normalize_private_key(private_key)
        
        # We don't store the private_key in state anymore for security.
        # We check against the environment variable or if the token is missing.
        if (app_cfg.get("app_id") != str(app_id) or 
            app_cfg.get("installation_id") != str(inst_id) or
            not s.get("github_token")):
            
            res = configure_github_app(app_id, clean_key, inst_id, tool_context=tool_context)
            if res.get("status") == "error":
                # Log error in state to make it visible to user/orchestrator
                s["last_auto_auth_error"] = res.get("message")

    # 2. Try to load from repo if present (overrides everything else)
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


def _normalize_private_key(key: str) -> str:
    """
    Robustly normalize a PEM private key from various formats.
    """
    if not key:
        return ""
    
    # 1. Handle escaped newlines (e.g. from .env parsing)
    clean = key.replace("\\n", "\n").replace("\\r", "").strip()
    
    # 2. If it's a smashed single line but has PEM headers, try to restore structure
    if "-----BEGIN" in clean and "\n" not in clean:
        # Common headers
        headers = [
            "-----BEGIN RSA PRIVATE KEY-----",
            "-----BEGIN PRIVATE KEY-----",
            "-----BEGIN ANY PRIVATE KEY-----",
            "-----BEGIN OPENSSH PRIVATE KEY-----"
        ]
        footers = [
            "-----END RSA PRIVATE KEY-----",
            "-----END PRIVATE KEY-----",
            "-----END ANY PRIVATE KEY-----",
            "-----END OPENSSH PRIVATE KEY-----"
        ]
        
        for h in headers:
            if h in clean:
                clean = clean.replace(h, h + "\n")
        for f in footers:
            if f in clean:
                clean = clean.replace(f, "\n" + f)

    # 3. Ensure it ends with exactly one newline
    return clean.strip() + "\n"


def configure_github_app(app_id: str, private_key: str, installation_id: str, tool_context=None) -> Dict[str, Any]:
    """
    Configure and authenticate using a GitHub App installation.
    - app_id: The ID of the GitHub App
    - private_key: The content of the App's private key (.pem)
    - installation_id: The installation ID for the target repository/org
    This tool generates an installation token and stores it in session.state['github_token'].
    """
    # 0. Robust key normalization
    clean_key = _normalize_private_key(private_key)

    # 1. Create a JWT for the GitHub App
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": str(app_id), # Ensure it's a string
    }
    try:
        encoded_jwt = jwt.encode(payload, clean_key, algorithm="RS256")
    except Exception as e:
        return {"status": "error", "message": f"JWT encoding failed. Check if your GITHUB_APP_PRIVATE_KEY is a valid RSA private key. Error: {e}"}

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

        # Store in state (excluding private_key for security). 
        # This is session-only and NOT persisted to repo state.json.
        tool_context.state["github_app"] = {
            "app_id": str(app_id),
            "installation_id": str(installation_id),
            "expires_at": token_data.get("expires_at"),
        }
        tool_context.state["github_token"] = token
        return {"status": "ok", "message": "GitHub App authenticated successfully", "expires_at": token_data.get("expires_at")}
    except Exception as e:
        return {"status": "error", "message": f"Failed to get installation token: {e}"}


def create_litellm_virtual_key(agent_name: str, max_budget: float = None, budget_duration: str = None, tool_context=None) -> Dict[str, Any]:
    """
    Create a LiteLLM Virtual Key for a specific agent with an optional budget.
    Requires LITELLM_MASTER_KEY to be set in environment and the proxy to be running.
    - max_budget: optional maximum budget in USD for this key.
    - budget_duration: optional duration for the budget (e.g., "1h", "1d", "1m").
    """
    master_key = os.environ.get("LITELLM_MASTER_KEY")
    proxy_base = os.environ.get("LITELLM_PROXY_API_BASE", "http://localhost:4000")
    
    if not master_key:
        return {"status": "error", "message": "LITELLM_MASTER_KEY not found in environment."}
    
    # 1. Ensure a Shared Budget exists in LiteLLM for visibility
    # We use a fixed ID for the sprint budget to group agents
    budget_id = "scrum-sprint-budget"
    
    # Best-effort creation of the budget object in LiteLLM
    try:
        # Check if budget exists first
        get_resp = requests.get(
            f"{proxy_base}/budget/info?budget_id={budget_id}",
            headers={"Authorization": f"Bearer {master_key}"},
            timeout=5
        )
        
        # Determine target budget
        total_budget_usd = tool_context.state.get("budgets", {}).get("total_usd") or 10.0
        
        if get_resp.status_code == 200:
            # Update existing budget
            requests.post(
                f"{proxy_base}/budget/update",
                headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
                json={
                    "budget_id": budget_id,
                    "max_budget": total_budget_usd
                },
                timeout=5
            )
        else:
            # Create new budget
            requests.post(
                f"{proxy_base}/budget/new",
                headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
                json={
                    "budget_id": budget_id,
                    "max_budget": total_budget_usd,
                    "budget_duration": "30d"
                },
                timeout=5
            )
    except Exception:
        pass # Might fail if proxy is down or busy
    
    # 2. Generate the Key
    url = f"{proxy_base}/key/generate"
    headers = {
        "Authorization": f"Bearer {master_key}",
        "Content-Type": "application/json"
    }
    
    # Map agent name to relevant models
    models = ["scrum-po", "scrum-sm", "scrum-dev", "scrum-qa", "scrum-arch", "scrum-orchestrator"]
    
    data = {
        "models": models,
        "metadata": {"agent": agent_name},
        "key_alias": f"key-{agent_name.lower()}",
        "budget_id": budget_id # Link key to the budget object for visibility
    }
    
    if max_budget is not None:
        data["max_budget"] = max_budget
    if budget_duration:
        data["budget_duration"] = budget_duration
    
    try:
        resp = requests.post(url, headers=headers, json=data, timeout=10)
        resp.raise_for_status()
        res = resp.json()
        key = res.get("key")
        
        if not key:
            return {"status": "error", "message": "No key returned from LiteLLM proxy."}
        
        # Store in state (Session-only, NOT persisted to repo state.json)
        keys = tool_context.state.get("litellm_keys", {})
        keys[agent_name] = key
        tool_context.state["litellm_keys"] = keys
        
        return {"status": "ok", "agent": agent_name, "key": key, "max_budget": max_budget, "budget_duration": budget_duration, "budget_id": budget_id}
    except Exception as e:
        return {"status": "error", "message": f"Failed to generate LiteLLM key: {e}"}


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
            
            content += "\n<!-- AGENT SAFEGUARD: This README reflects the current product vision and goals. Before proposing changes, check the decision log and existing docs. -->\n"
            
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
        if tool_context.state.get("last_auto_auth_error"):
            diagnostics["auto_auth_error"] = tool_context.state.get("last_auto_auth_error")
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
            # Ensure git uses the token for HTTPS
            # We configure it globally for the process context if needed, 
            # but usually setting the env var is enough for gh and modern git.
            # To be extra safe for all git operations:
            _ = subprocess.run(
                ["git", "config", "--global", "http.https://github.com/.extraheader", f"AUTHORIZATION: basic {token}"],
                cwd=cwd or str(_project_root()),
                env=env,
                capture_output=True
            )

        # Set git user name/email based on current agent if available
        agent_name = getattr(tool_context, "agent_name", None)
        if agent_name:
            env["GIT_AUTHOR_NAME"] = agent_name
            env["GIT_AUTHOR_EMAIL"] = f"{agent_name.lower().replace(' ', '_')}@horseless-carriage.local"
            env["GIT_COMMITTER_NAME"] = agent_name
            env["GIT_COMMITTER_EMAIL"] = f"{agent_name.lower().replace(' ', '_')}@horseless-carriage.local"

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

def update_budgets(total: int = None, total_usd: float = None, agent_budgets: Dict[str, int] = None, tool_context=None) -> Dict[str, Any]:
    """
    Update the total and per-agent token budgets.
    - total: token budget
    - total_usd: USD budget for LiteLLM proxy
    """
    s = tool_context.state
    budgets = s.get("budgets", {"total": 0, "agents": {}})
    if total is not None:
        budgets["total"] = total
    if total_usd is not None:
        budgets["total_usd"] = total_usd
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
        "remaining_total_tokens": budgets.get("total", 0) - usage.get("total", 0),
        "total_usd": budgets.get("total_usd"),
        "is_over_budget_tokens": usage.get("total", 0) >= budgets.get("total", 0) if budgets.get("total", 0) > 0 else False
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
    budgets = s.get("budgets", {"total": 0, "agents": {}})
    usage = s.get("token_usage", {"total": 0, "agents": {}})
    retro_actions = s.get("retro_actions", [])
    
    report = f"# Sprint Review Report\n\n## Summary\n{summary}\n\n## Accomplishments\n"
    for a in accomplishments:
        report += f"- {a}\n"
    
    report += f"\n## Budget and Usage\n- Token Budget: {budgets.get('total', 0)}\n- Token Usage: {usage['total']}\n"
    if budgets.get("total_usd"):
        report += f"- USD Budget (LiteLLM): ${budgets.get('total_usd'):.2f}\n"
    
    report += "\n### Per-Agent Token Usage\n"
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

def gh_pr_comment(body: str, pr_id: str | int | None = None, tool_context=None) -> Dict[str, Any]:
    """
    Add a comment to a Pull Request.
    - body: the comment text.
    - pr_id: optional PR number, URL, or branch. If None, uses current branch.
    """
    repo_root = str(_configured_repo_root(tool_context))
    
    # Prefix with agent role if available
    agent_name = getattr(tool_context, "agent_name", None)
    prefix = f"**{agent_name}:** " if agent_name else ""
    full_body = prefix + body
    
    cmd = ["gh", "pr", "comment"]
    if pr_id:
        cmd.append(str(pr_id))
    cmd.extend(["--body", full_body])
    
    r = _run(cmd, cwd=repo_root, tool_context=tool_context)
    return r

def gh_pr_review(body: str, event: str = "COMMENT", pr_id: str | int | None = None, tool_context=None) -> Dict[str, Any]:
    """
    Add a review to a Pull Request.
    - body: the review body text.
    - event: the review action (COMMENT, APPROVE, REQUEST_CHANGES).
    - pr_id: optional PR number, URL, or branch. If None, uses current branch.
    """
    repo_root = str(_configured_repo_root(tool_context))
    
    # Prefix with agent role if available
    agent_name = getattr(tool_context, "agent_name", None)
    prefix = f"**{agent_name}:** " if agent_name else ""
    full_body = prefix + body
    
    cmd = ["gh", "pr", "review"]
    if pr_id:
        cmd.append(str(pr_id))
    
    cmd.extend(["--body", full_body])
    
    if event.upper() == "APPROVE":
        cmd.append("--approve")
    elif event.upper() == "REQUEST_CHANGES":
        cmd.append("--request-changes")
    else:
        cmd.append("--comment")
        
    r = _run(cmd, cwd=repo_root, tool_context=tool_context)
    return r

def gh_pr_check_logs(pr_id: str | int | None = None, check_name: str | None = None, tool_context=None) -> Dict[str, Any]:
    """
    Fetch logs for a specific PR check (GitHub Action/Workflow).
    - pr_id: optional PR number, URL, or branch. If None, uses current branch.
    - check_name: optional substring to filter by check name.
    """
    repo_root = str(_configured_repo_root(tool_context))

    # 1. Get check list with links
    cmd = ["gh", "pr", "checks"]
    if pr_id:
        cmd.append(str(pr_id))
    cmd.extend(["--json", "name,link,state,bucket"])

    r = _run(cmd, cwd=repo_root, tool_context=tool_context)
    if r.get("status") == "error":
        return r

    try:
        checks = json.loads(r["stdout"])
        if not checks:
            return {"status": "error", "message": "No checks found for this PR."}

        # 2. Filter by check_name if provided
        target_check = None
        if check_name:
            for c in checks:
                if check_name.lower() in c.get("name", "").lower():
                    target_check = c
                    break
            if not target_check:
                return {"status": "error", "message": f"No check found matching '{check_name}'. Available: {[c.get('name') for c in checks]}"}
        else:
            # If no name, pick the first failing one, or just the first one
            failing = [c for c in checks if c.get("bucket") == "fail"]
            target_check = failing[0] if failing else checks[0]

        # 3. Extract Run ID from link
        # Example link: https://github.com/OWNER/REPO/actions/runs/12345678/job/98765432
        link = target_check.get("link", "")
        if "/actions/runs/" not in link:
            return {"status": "error", "message": f"Could not find GitHub Actions Run ID in link: {link}"}

        parts = link.split("/actions/runs/")
        run_id = parts[1].split("/")[0]

        # 4. Fetch logs using `gh run view <run_id> --log`
        log_cmd = ["gh", "run", "view", run_id, "--log"]
        log_res = _run(log_cmd, cwd=repo_root, tool_context=tool_context)
        
        return {
            "status": "ok" if log_res.get("status") == "ok" else "error",
            "check_name": target_check.get("name"),
            "state": target_check.get("state"),
            "run_id": run_id,
            "logs": log_res.get("stdout", ""),
            "stderr": log_res.get("stderr", "")
        }

    except Exception as e:
        return {"status": "error", "message": f"Failed to parse or fetch logs: {e}", "details": r}