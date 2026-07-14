# agents/scrum_team/tools/scrum.py
from __future__ import annotations
import os
import json
from typing import Any, Dict, List
from .base import _configured_repo_root, _state_file_path, _project_root

DEFAULT_DOD = [
    "Code reviewed",
    "Automated tests passing",
    "Acceptance criteria met",
    "No critical security issues",
    "Docs updated if needed",
]

REPO_STATE_KEYS = [
    "version",
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

def init_scrum_state(tool_context=None) -> Dict[str, Any]:
    """
    Initialize all Scrum artifacts in session.state if missing.
    """
    from .github import configure_github_app
    from .requirements import sync_stories_from_markdown
    from .base import _normalize_private_key
    
    s = tool_context.state
    
    from ..state import ScrumState
    default_version = ScrumState().version

    # Initialize defaults
    s.setdefault("version", default_version)
    s.setdefault("product_vision", "")
    s.setdefault("product_goals", [])
    s.setdefault("product_backlog", [])
    s.setdefault("definition_of_done", list(DEFAULT_DOD))
    s.setdefault("sprint_goal", "")
    s.setdefault("sprint_backlog", [])
    s.setdefault("impediment_log", [])
    s.setdefault("retro_actions", [])
    s.setdefault("decision_log", [])
    s.setdefault("sprint_report", "")
    s.setdefault("budgets", {"total": 0, "total_usd": 0.0, "agents": {}})
    s.setdefault("token_usage", {"total": 0, "agents": {}})
    s.setdefault("story_estimates", {})

    # 1. Try to load from repo if present first, so environment can override
    try:
        repo_root = _configured_repo_root(tool_context)
        fp = _state_file_path(repo_root)
        if fp.exists():
            _ = load_state_from_repo(tool_context)
    except Exception:
        pass

    # 2. Load/Override from environment variables
    # (Environment variables take precedence over persisted state for configuration)
    
    # Budgets
    budgets = s.get("budgets", {}) or {}
    env_token_budget = os.environ.get("SPRINT_TOKEN_BUDGET")
    if env_token_budget:
        try:
            budgets["total"] = int(env_token_budget)
        except (ValueError, TypeError):
            pass
    
    env_usd_budget = os.environ.get("SPRINT_USD_BUDGET")
    if env_usd_budget:
        try:
            budgets["total_usd"] = float(env_usd_budget)
        except (ValueError, TypeError):
            pass
    s["budgets"] = budgets

    # Repo
    env_repo_url = os.environ.get("GITHUB_REPO_URL")
    if env_repo_url:
        repo_cfg = s.get("repo", {}) or {}
        repo_cfg["url"] = env_repo_url
        repo_cfg["local_path"] = os.environ.get("STATE_REPO_PATH") or repo_cfg.get("local_path") or ""
        repo_cfg["default_branch"] = os.environ.get("GITHUB_REPO_BRANCH") or repo_cfg.get("default_branch") or "main"
        s["repo"] = repo_cfg

    # 2.1 Load GitHub App credentials
    app_id = os.environ.get("GITHUB_APP_ID")
    private_key = os.environ.get("GITHUB_APP_PRIVATE_KEY")
    inst_id = os.environ.get("GITHUB_APP_INSTALLATION_ID")
    
    if app_id and private_key and inst_id:
        app_cfg = s.get("github_app", {})
        clean_key = _normalize_private_key(private_key)
        # We check against strings to avoid type issues
        if (app_cfg.get("app_id") != str(app_id) or 
            app_cfg.get("installation_id") != str(inst_id) or
            not s.get("github_token")):
            
            res = configure_github_app(app_id, clean_key, inst_id, tool_context=tool_context)
            if res.get("status") == "error":
                s["last_auto_auth_error"] = res.get("message")
    
    # 2.2 Fallback to GITHUB_TOKEN if provided and no app token set
    env_github_token = os.environ.get("GITHUB_TOKEN")
    if env_github_token and not s.get("github_token"):
        s["github_token"] = env_github_token

    # 3. Load stories and requirements from Markdown
    try:
        from .requirements import sync_stories_from_markdown, sync_requirements_from_markdown
        _ = sync_stories_from_markdown(tool_context)
        _ = sync_requirements_from_markdown(tool_context)
    except Exception:
        pass

    return {"status": "ok", "initialized": True}

def save_state_to_repo(tool_context=None) -> Dict[str, Any]:
    """
    Persist selected scrum state keys into the configured repo.
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
    Load previously persisted scrum state.
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
    Add/update an item in sprint_backlog with implementation plan fields.
    """
    from .requirements import _update_story_markdown
    s = tool_context.state
    sprint: List[Dict[str, Any]] = list(s.get("sprint_backlog", []))

    estimates = s.get("story_estimates", {})
    if "estimate" in plan:
        estimates[title_or_id] = plan["estimate"]
        s["story_estimates"] = estimates

    updated_item = None
    key = title_or_id
    for i, x in enumerate(sprint):
        if x.get("id") == key or x.get("title") == key:
            sprint[i] = {**x, **plan}
            s["sprint_backlog"] = sprint
            updated_item = sprint[i]
            break

    if not updated_item:
        entry = {"title": key, **plan}
        sprint.append(entry)
        s["sprint_backlog"] = sprint
        updated_item = entry

    _ = save_state_to_repo(tool_context)
    _update_story_markdown(updated_item, tool_context)
    return {"status": "ok", "updated": (updated_item != ({"title": key, **plan})), "item": updated_item}
