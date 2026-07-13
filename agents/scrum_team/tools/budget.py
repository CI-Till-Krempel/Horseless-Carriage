# agents/scrum_team/tools/budget.py
from __future__ import annotations
import os
import requests
from typing import Any, Dict, List
from .base import _state_file_path, _configured_repo_root
from ..helpers import get_process_overhead_percentage

def update_budgets(total_usd: float = None, tool_context=None) -> Dict[str, Any]:
    """
    Update the total USD budget for the sprint.
    - total_usd: USD budget for LiteLLM proxy
    """
    from .scrum import save_state_to_repo
    s = tool_context.state
    budgets = s.get("budgets", {})
    if total_usd is not None:
        budgets["total_usd"] = total_usd
    s["budgets"] = budgets
    save_state_to_repo(tool_context)
    return {"status": "ok", "budgets": budgets}

def get_budget_status(tool_context=None) -> Dict[str, Any]:
    """
    Return the current budget usage and status.
    """
    s = tool_context.state
    budgets = s.get("budgets", {})
    usage = s.get("token_usage", {"total": 0, "agents": {}})
    
    status = {
        "budgets": budgets,
        "usage": usage,
        "total_usd": budgets.get("total_usd"),
    }
    return {"status": "ok", "budget_status": status}

def log_token_usage(agent_name: str, tokens: int, tool_context=None) -> Dict[str, Any]:
    """
    Manually log token usage for an agent (e.g. after a meeting or if automatic tracking is missing).
    """
    from .scrum import save_state_to_repo
    s = tool_context.state
    usage = s.get("token_usage", {"total": 0, "agents": {}})
    
    usage["total"] += tokens
    usage["agents"][agent_name] = usage["agents"].get(agent_name, 0) + tokens
    
    s["token_usage"] = usage
    save_state_to_repo(tool_context)
    return {"status": "ok", "usage": usage}

def create_litellm_virtual_key(agent_name: str, max_budget: float = None, budget_duration: str = None, tool_context=None) -> Dict[str, Any]:
    """
    Generate a LiteLLM Virtual Key for a specific agent role with an optional budget.
    """
    master_key = os.environ.get("LITELLM_MASTER_KEY")
    proxy_base = os.environ.get("LITELLM_PROXY_API_BASE", "http://localhost:4000")
    
    if not master_key:
        return {"status": "error", "message": "LITELLM_MASTER_KEY environment variable not set."}

    budget_id = "scrum-sprint-budget"
    
    # 1. Ensure the shared budget object exists in LiteLLM
    try:
        get_resp = requests.get(
            f"{proxy_base}/budget/info?budget_id={budget_id}",
            headers={"Authorization": f"Bearer {master_key}"},
            timeout=5
        )
        
        total_budget_usd = tool_context.state.get("budgets", {}).get("total_usd") or 10.0
        
        if get_resp.status_code == 200:
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
        pass 
    
    # 2. Generate the Key
    url = f"{proxy_base}/key/generate"
    headers = {
        "Authorization": f"Bearer {master_key}",
        "Content-Type": "application/json"
    }
    
    models = ["scrum-po", "scrum-sm", "scrum-dev", "scrum-qa", "scrum-arch", "scrum-orchestrator"]
    
    data = {
        "models": models,
        "metadata": {"agent": agent_name},
        "key_alias": f"key-{agent_name.lower()}",
        "budget_id": budget_id 
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
        
        keys = tool_context.state.get("litellm_keys", {})
        keys[agent_name] = key
        tool_context.state["litellm_keys"] = keys
        
        return {"status": "ok", "agent": agent_name, "key": key, "max_budget": max_budget, "budget_duration": budget_duration, "budget_id": budget_id}
    except Exception as e:
        return {"status": "error", "message": f"Failed to generate LiteLLM key: {e}"}

def create_sprint_report(summary: str, accomplishments: List[str], tool_context=None) -> Dict[str, Any]:
    """
    Generate a management summary report for the current sprint.
    """
    from .docs import write_file
    s = tool_context.state
    budgets = s.get("budgets", {})
    usage = s.get("token_usage", {"total": 0})
    retro = s.get("retro_actions", [])
    
    report = f"# Sprint Review Report\n\n## Summary\n{summary}\n\n## Accomplishments\n"
    for item in accomplishments:
        report += f"- {item}\n"
        
    report += f"\n## Budget and Usage\n"
    if budgets.get("total_usd"):
        report += f"- USD Budget (LiteLLM): ${budgets.get('total_usd'):.2f}\n"
    
    report += f"- Process Overhead: {get_process_overhead_percentage()}%\n"
    
    report += "\n### Per-Agent Token Usage\n"
    for agent, agent_usage in usage.get("agents", {}).items():
        report += f"  - {agent}: {agent_usage}\n"
        
    if retro:
        report += "\n## Retrospective Actions (including efficiency improvements)\n"
        for action in retro:
            report += f"- {action['action']} (Owner: {action['owner']}, Status: {action['status']})\n"
            
    # Include story estimates if present
    estimates = s.get("story_estimates", {})
    if estimates:
        report += "\n## Story Estimates (Tokens)\n"
        for title, estimate in estimates.items():
            report += f"- {title}: {estimate}\n"
            
    s["sprint_report"] = report
    path = "docs/reports/SPRINT-REPORT-LATEST.md"
    write_file(path, report, overwrite=True, tool_context=tool_context)
    
    return {"status": "ok", "report": report, "path": path}

def calculate_cost_breakdown(tool_context=None) -> Dict[str, Any]:
    """
    Calculates the cost breakdown of the specific roles and the percentage of tokens used for feature implementation.
    """
    s = tool_context.state
    usage = s.get("token_usage", {"total": 0, "agents": {}})
    total_tokens = usage.get("total", 1)  # Avoid division by zero

    cost_breakdown = {
        "per_role": usage.get("agents", {}),
        "feature_implementation_percentage": (
            usage.get("agents", {}).get("DevTeam", 0) / total_tokens
        )
        * 100,
    }
    return {"status": "ok", "cost_breakdown": cost_breakdown}

def recommend_sprint_budget(tool_context=None) -> Dict[str, Any]:
    """
    Recommends a sprint budget based on historical data.
    """
    # In a real implementation, this would analyze historical data to provide a more accurate recommendation.
    return {"status": "ok", "recommended_budget": 10.0}

def optimize_process_for_budget(tool_context=None) -> Dict[str, Any]:
    """
    Optimizes the amount of overhead spent on process based on the sprint budget.
    """
    s = tool_context.state
    budgets = s.get("budgets", {})
    
    process_overhead_percentage = get_process_overhead_percentage()
    
    if budgets.get("total_usd", 0) * (process_overhead_percentage / 100) < 2:
        # Lightweight process for small budgets
        process_optimizations = [
            "Reduced number of meetings",
            "Simplified reporting",
        ]
    else:
        # Standard process for larger budgets
        process_optimizations = []

    return {"status": "ok", "process_optimizations": process_optimizations}