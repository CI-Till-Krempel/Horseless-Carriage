# agents/scrum_team/tools/scrum.py
from __future__ import annotations
import os
import json
from typing import Any, Dict, List
from .base import _configured_repo_root, _state_file_path, _project_root, _hc_version
from .migrations import migrate_state
from ..helpers import blocks_direct_status_set, is_low_quality_retro_text, new_sprint_item_blocked

DEFAULT_DOD = [
    "Code reviewed",
    "Automated tests passing",
    "Acceptance criteria met",
    "No critical security issues",
    "Docs updated if needed",
    "specs/ROADMAP.md updated via update_roadmap to reflect this story's completed status",
    "Actual tokens spent logged via log_story_tokens",
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
    "sprint_report_kpis",
    "repo",
    "messages",
    "transcript",
    "hc_version",
    "retro_baseline",
    "human_approvals",
    "sprint_approval_baseline",
    "release_approval_baseline",
    "dev_touch_baseline",
    "last_check_build",
    "pr_review_calls",
    "architect_review_baseline",
    "qa_review_baseline",
    "sprint_report_pending_release",
]
# Deliberately excluded from the above: github_token, github_app,
# litellm_keys, last_auto_auth_error - these are real secrets/session-only
# auth material and must never be written into the target repo's
# .hc/state.json. See SECURITY.md.

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
    s.setdefault("transcript", [])
    s.setdefault("retro_baseline", 0)
    s.setdefault("human_approvals", [])
    s.setdefault("sprint_approval_baseline", 0)
    s.setdefault("release_approval_baseline", 0)
    s.setdefault("dev_touch_baseline", 0)
    s.setdefault("last_check_build", None)
    s.setdefault("pr_review_calls", {})
    s.setdefault("architect_review_baseline", 0)
    s.setdefault("qa_review_baseline", 0)
    s.setdefault("sprint_report_pending_release", False)

    # 1. Try to load from repo if present first, so environment can override
    try:
        repo_root = _configured_repo_root(tool_context)
        fp = _state_file_path(repo_root)
        if fp.exists():
            _ = load_state_from_repo(tool_context)
    except Exception:
        pass

    # hc_version always reflects the version actually running this session,
    # never the (possibly older) value just loaded from the state repo -
    # see RELEASE.md "Tracking the HC version in the state repo".
    s["hc_version"] = _hc_version()

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
    
    # HARD GUARDRAIL: Never allow 0 budget if not explicitly intended (and even then, discourage it)
    # Default to sensible values if still 0
    if budgets.get("total", 0) <= 0:
        budgets["total"] = 1000000  # Default 1M tokens
    if budgets.get("total_usd", 0.0) <= 0.0:
        budgets["total_usd"] = 10.0 # Default $10.00
        
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
        data = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict):
            return {"status": "error", "message": "Invalid state format in state.json"}
        data = migrate_state(data, data.get("hc_version", "unknown"))
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
    if is_low_quality_retro_text(description):
        return {
            "status": "error",
            "message": (
                "description is blank, a generic placeholder, or too short to be a concrete "
                "impediment (see SM_PROMPT's RETROSPECTIVE REASONING) - describe what actually "
                "blocked the process this sprint."
            ),
        }
    s = tool_context.state
    imp = {"description": description.strip(), "owner": owner.strip(), "status": "open"}
    s["impediment_log"] = list(s.get("impediment_log", [])) + [imp]
    _ = save_state_to_repo(tool_context)
    return {"status": "ok", "impediment": imp}

def add_retro_action(action: str, owner: str, success_metric: str, tool_context=None) -> Dict[str, Any]:
    """
    Add an action item from retrospectives.
    """
    if is_low_quality_retro_text(action) or is_low_quality_retro_text(success_metric):
        return {
            "status": "error",
            "message": (
                "action/success_metric is blank, a generic placeholder ('communicate better' and "
                "similar), or too short - SM_PROMPT's RETROSPECTIVE REASONING requires an action "
                "tied to what actually happened this sprint, with a real success metric, not a "
                "formality to unblock create_sprint_report."
            ),
        }
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

_APPROVAL_TYPES = ("sprint", "release", "budget")


def record_human_approval(approval_type: str, note: str = "", tool_context=None) -> Dict[str, Any]:
    """
    Records an explicit human approval event (see ORCHESTRATOR_PROMPT's
    ITERATION MODE: "A sprint can ONLY start after explicit human review and
    approval of the sprint goal and sprint backlog", and PO/SM_PROMPT's
    "Ensure Human Review is done for each increment") - the mechanical
    counterpart `advance_story_stage(..., "Implemented")` and
    `create_release_pr` (see ISSUE-0001) actually check for, instead of
    trusting the model's own assertion that a human reviewed something.
    - approval_type: "sprint" (this sprint's goal + backlog), "release"
      (this increment, before create_release_pr), or "budget" (this
      sprint's token/USD budget - required instead of "sprint" at the CEO
      interaction level). Which of these is actually required by the two
      gates above depends on INTERACTION_LEVEL - see
      docs/INTERACTION-LEVELS.md and
      agents/scrum_team/helpers.py's required_pre_implementation_approval/
      required_pre_release_approval. A rejected gate's own error message
      names exactly which type to call this with.
    """
    if approval_type not in _APPROVAL_TYPES:
        return {"status": "error", "message": f"Unknown approval_type '{approval_type}'. Must be one of {_APPROVAL_TYPES}."}
    s = tool_context.state
    entry = {"type": approval_type, "note": note.strip()}
    s["human_approvals"] = list(s.get("human_approvals", [])) + [entry]
    _ = save_state_to_repo(tool_context)
    return {"status": "ok", "approval": entry}

def plan_sprint_backlog_item(title_or_id: str, plan: Dict[str, Any], tool_context=None) -> Dict[str, Any]:
    """
    Add/update an item in sprint_backlog with implementation plan fields.
    """
    from .requirements import _update_story_markdown

    if blocks_direct_status_set(plan.get("status")):
        return {
            "status": "error",
            "message": (
                f"Cannot set status to '{plan.get('status')}' directly - stage transitions (and "
                "legacy 'Done'/'completed'/'closed', which are treated as every stage complete) "
                "must go through advance_story_stage(title_or_id, stage), which enforces ordering "
                "and stage ownership. Omit 'status' here and call advance_story_stage instead."
            ),
        }

    s = tool_context.state
    sprint: List[Dict[str, Any]] = list(s.get("sprint_backlog", []))

    estimates = s.get("story_estimates", {})
    if "estimate" in plan:
        # {estimate, actual} shape - actual is filled in later via
        # log_story_tokens (agents/scrum_team/tools/budget.py). A bare
        # number here is the old pre-actual-tracking shape, where the
        # stored value was always the estimate - keep it as one rather
        # than misreading it as an actual.
        entry = estimates.get(title_or_id)
        entry = entry if isinstance(entry, dict) else {}
        entry["estimate"] = plan["estimate"]
        estimates[title_or_id] = entry
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
        # ISSUE-0010: this is a genuinely new sprint_backlog item (not an
        # update to one already there) - refuse it if the previous sprint's
        # close sequence (retro/report done, but release PR never
        # completed) is still hanging open.
        block_msg = new_sprint_item_blocked(s)
        if block_msg:
            return {"status": "error", "message": block_msg}

        # Inherit title/user_story/acceptance_criteria/type from the matching
        # product_backlog entry (PO-owned, via upsert_story) rather than
        # defaulting title to the bare lookup key - that default is exactly
        # what produced story files literally titled "US-0008" in real eval
        # runs (plan_sprint_backlog_item's own plan dict never carries those
        # PO-owned fields at all).
        product_match = next(
            (x for x in s.get("product_backlog", []) if x.get("id") == key or x.get("title") == key),
            {},
        )
        entry = {**product_match, **plan}
        entry.setdefault("title", key)
        sprint.append(entry)
        s["sprint_backlog"] = sprint
        updated_item = entry

    _ = save_state_to_repo(tool_context)
    story_md_result = _update_story_markdown(updated_item, tool_context)
    if story_md_result.get("status") != "ok":
        # Surface the failure (e.g. a Definition-of-Ready rejection) instead
        # of silently discarding it - a caller that only checks this
        # top-level status must be able to see the story file wasn't
        # actually written.
        return {"status": "error", "message": story_md_result.get("message"), "item": updated_item, "story_markdown": story_md_result}
    return {"status": "ok", "updated": (updated_item != ({"title": key, **plan})), "item": updated_item, "story_markdown": story_md_result}
