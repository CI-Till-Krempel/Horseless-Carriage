# agents/scrum_team/tools/budget.py
from __future__ import annotations
import math
import os
import re
import requests
from typing import Any, Dict, List
from .base import _state_file_path, _configured_repo_root
from ..helpers import get_process_overhead_percentage, is_story_done

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

def log_story_tokens(title_or_id: str, actual_tokens: int, tool_context=None) -> Dict[str, Any]:
    """
    Records the actual tokens spent implementing a story, alongside its
    estimate (see plan_sprint_backlog_item's `estimate` field), so
    create_sprint_report can show estimate-vs-actual per story instead of
    just the estimate - part of Definition of Done (see spec-templates/
    DOD.md): log this for a story before marking it Done.
    """
    from .scrum import save_state_to_repo
    s = tool_context.state
    estimates = s.get("story_estimates", {})
    entry = estimates.get(title_or_id)
    # Older/other callers may still leave a bare number here (the original
    # shape, before actuals existed) - normalize to a dict without losing it.
    entry = entry if isinstance(entry, dict) else ({"estimate": entry} if entry is not None else {})
    entry["actual"] = actual_tokens
    estimates[title_or_id] = entry
    s["story_estimates"] = estimates
    save_state_to_repo(tool_context)
    return {"status": "ok", "story_estimates": estimates}

def create_litellm_virtual_key(agent_name: str, max_budget: float = None, budget_duration: str = None, models: List[str] = None, tool_context=None) -> Dict[str, Any]:
    """
    Generate a LiteLLM Virtual Key for a specific agent role with an optional budget.
    """
    master_key = os.environ.get("LITELLM_MASTER_KEY")
    proxy_base = os.environ.get("LITELLM_PROXY_API_BASE", "http://litellm:4000")
    
    if not master_key:
        return {"status": "error", "message": "LITELLM_MASTER_KEY environment variable not set."}

    budget_id = "scrum-sprint-budget"
    
    # 1. Ensure the shared budget object exists in LiteLLM
    try:
        get_resp = requests.post(
            f"{proxy_base}/budget/info",
            headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
            json={"budgets": [budget_id]},
            timeout=5
        )
        get_resp.raise_for_status()
        
        # HARD GUARDRAIL: Use state budget if available, otherwise fallback to environment or safe default
        total_budget_usd = tool_context.state.get("budgets", {}).get("total_usd")
        if not total_budget_usd or total_budget_usd <= 0:
            try:
                total_budget_usd = float(os.environ.get("SPRINT_USD_BUDGET", 10.0))
            except (ValueError, TypeError):
                total_budget_usd = 10.0
        
        # Check if the budget exists in the returned list
        exists = False
        budget_info_list = get_resp.json()
        if budget_info_list and isinstance(budget_info_list, list) and len(budget_info_list) > 0:
            exists = True
        
        if exists:
            upd_resp = requests.post(
                f"{proxy_base}/budget/update",
                headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
                json={
                    "budget_id": budget_id,
                    "max_budget": total_budget_usd
                },
                timeout=5
            )
            upd_resp.raise_for_status()
        else:
            new_resp = requests.post(
                f"{proxy_base}/budget/new",
                headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
                json={
                    "budget_id": budget_id,
                    "max_budget": total_budget_usd,
                    "budget_duration": "30d"
                },
                timeout=5
            )
            new_resp.raise_for_status()
    except Exception as e:
        return {"status": "error", "message": f"Budget API error: {e}"}
    
    # 2. Generate the Key
    url = f"{proxy_base}/key/generate"
    headers = {
        "Authorization": f"Bearer {master_key}",
        "Content-Type": "application/json"
    }
    
    if models is None:
        models = [
            "scrum-po", "scrum-sm", "scrum-dev", "scrum-qa", 
            "scrum-arch", "scrum-orchestrator", "scrum-quality"
        ]
    
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
        if resp.status_code != 200:
            return {"status": "error", "message": f"Failed to generate LiteLLM key: {resp.status_code} {resp.text}"}
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

def _summarize_transcript(transcript: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Condenses a transcript to one representative (most recent) entry per
    agent, ordered by each agent's first appearance. A plain tail-N cut would
    let one chatty agent crowd out every other agent's key contribution;
    this guarantees every agent that spoke is represented.
    """
    order: List[str] = []
    last_by_agent: Dict[str, Dict[str, Any]] = {}
    for entry in transcript:
        agent_name = entry.get("agent_name", "unknown")
        if agent_name not in last_by_agent:
            order.append(agent_name)
        last_by_agent[agent_name] = entry
    return [last_by_agent[name] for name in order]

def _sprint_length_feedback(s: Dict[str, Any]) -> str:
    """
    Advisory-only: flags when this sprint looks budget-starved (hit its
    token cap with stories still unfinished) and suggests a new per-sprint
    token budget extrapolated from the observed tokens-per-completed-story
    rate. Never applied automatically - a human must change the actual
    config (SPRINT_TOKEN_BUDGET / EVAL_SPRINT_TOKEN_BUDGET) - see README.md
    "Budget Management" / RELEASE.md "Team performance evaluation".
    """
    budgets = s.get("budgets", {})
    token_limit = budgets.get("total", 0)
    if token_limit <= 0:
        try:
            token_limit = int(os.environ.get("SPRINT_TOKEN_BUDGET", 1000000))
        except (ValueError, TypeError):
            token_limit = 1000000

    token_used = s.get("token_usage", {}).get("total", 0)
    backlog = s.get("sprint_backlog", []) or []
    total_items = len(backlog)
    done_items = len([i for i in backlog if is_story_done(i.get("status"))])
    unfinished = total_items - done_items
    pct = (token_used / token_limit * 100) if token_limit else 0.0

    lines = [
        "\n## Sprint Length Feedback\n",
        f"- Tokens used: {token_used:,} / {token_limit:,} ({pct:.0f}%)\n",
        f"- Stories: {done_items}/{total_items} completed this sprint\n",
    ]

    if total_items == 0:
        lines.append("- No sprint backlog recorded - nothing to assess.\n")
        return "".join(lines)
    if unfinished == 0:
        lines.append("- All planned stories were completed within budget - no sprint-length change suggested.\n")
        return "".join(lines)

    if token_limit > 0 and token_used >= 0.9 * token_limit:
        avg_per_done = token_used / done_items if done_items else token_used / total_items
        projected_total = avg_per_done * total_items
        suggested = math.ceil(projected_total * 1.2 / 50_000) * 50_000
        lines += [
            f"- This sprint used {pct:.0f}% of its token budget and left {unfinished}/{total_items} "
            "stories unfinished - the per-sprint token budget looks too small for the amount of work "
            "planned, not necessarily a quality problem.\n",
            f"- **Suggested new per-sprint token budget: ~{suggested:,} tokens** (extrapolated from "
            f"~{avg_per_done:,.0f} tokens/completed story x {total_items} planned stories, +20% headroom).\n",
            "- **This is a recommendation only - it is NOT applied automatically.** A human must "
            "approve it and set it manually (`SPRINT_TOKEN_BUDGET` / `EVAL_SPRINT_TOKEN_BUDGET`; see "
            "README.md \"Budget Management\").\n",
        ]
    else:
        remaining = max(token_limit - token_used, 0)
        lines.append(
            f"- {unfinished}/{total_items} stories are unfinished despite {remaining:,} tokens of "
            "budget headroom still available - the sprint-length token budget is likely NOT the "
            "cause; consider reviewing process/quality issues instead (see Top Problems / retro "
            "actions) rather than increasing the budget.\n"
        )
    return "".join(lines)


_SPRINT_REPORT_NUM_PATTERN = re.compile(r"SPRINT-REPORT-(\d+)\.md$")


def _next_sprint_report_path(tool_context) -> str:
    """
    Picks the next sequential specs/reports/SPRINT-REPORT-NNN.md path by
    scanning what's already there - self-healing like _generate_next_id,
    no separate counter in state to drift out of sync. Previously every
    sprint overwrote the same SPRINT-REPORT-LATEST.md, so only the final
    sprint's report ever survived in the repo.
    """
    repo_root = _configured_repo_root(tool_context)
    reports_dir = repo_root / "specs" / "reports"
    max_num = 0
    if reports_dir.exists():
        for fp in reports_dir.glob("SPRINT-REPORT-*.md"):
            m = _SPRINT_REPORT_NUM_PATTERN.match(fp.name)
            if m:
                max_num = max(max_num, int(m.group(1)))
    return f"specs/reports/SPRINT-REPORT-{max_num + 1:03d}.md"


def create_sprint_report(summary: str, accomplishments: List[str], tool_context=None) -> Dict[str, Any]:
    """
    Generate a management summary report for the current sprint.
    """
    from .docs import write_file
    s = tool_context.state
    budgets = s.get("budgets", {})
    usage = s.get("token_usage", {"total": 0})
    retro = s.get("retro_actions", [])
    
    hc_version = s.get("hc_version", "unknown")
    hc_version_line = f"Horseless Carriage v{hc_version}" if hc_version != "unknown" else "Horseless Carriage (version unknown)"
    report = f"# Sprint Review Report\n\n**Generated by {hc_version_line}**\n\n## Summary\n{summary}\n\n## Accomplishments\n"
    for item in accomplishments:
        report += f"- {item}\n"
        
    report += f"\n## Budget and Usage\n"
    if budgets.get("total_usd"):
        report += f"- USD Budget (LiteLLM): ${budgets.get('total_usd'):.2f}\n"
    
    report += f"- Process Overhead: {get_process_overhead_percentage()}%\n"
    
    report += "\n### Per-Agent Token Usage\n"
    for agent, agent_usage in usage.get("agents", {}).items():
        report += f"  - {agent}: {agent_usage}\n"

    report += _sprint_length_feedback(s)

    report += "\n## Retrospective Actions (including efficiency improvements)\n"
    if retro:
        for action in retro:
            report += f"- {action['action']} (Owner: {action['owner']}, Status: {action['status']})\n"
    else:
        # Deterministic, not reliant on the Scrum Master remembering to
        # mention it: a genuinely silent retro (no impediments, no
        # improvement ideas) is indistinguishable from one that just didn't
        # happen unless something says so explicitly - see SM_PROMPT's
        # RETROSPECTIVE REASONING, which requires add_retro_action every
        # sprint.
        report += (
            "**No retro actions recorded this sprint.** Scrum Master must call `add_retro_action` "
            "with at least one concrete improvement - see SM_PROMPT's RETROSPECTIVE REASONING. This "
            "either means the retrospective didn't happen, or it happened without producing anything "
            "actionable; either way it should not be silent.\n"
        )


    # Include story estimates if present
    estimates = s.get("story_estimates", {})
    if estimates:
        report += "\n## Story Estimates vs Actual Tokens\n"
        for title, entry in estimates.items():
            if isinstance(entry, dict):
                estimate = entry.get("estimate", "n/a")
                actual = entry.get("actual", "not logged")
            else:
                # Pre-actual-tracking shape: a bare number was always the estimate.
                estimate, actual = entry, "not logged"
            report += f"- {title}: estimate={estimate}, actual={actual}\n"

    # Link to the persisted multi-agent transcript (see US-0001/US-0002),
    # with a condensed per-agent excerpt so reviewers can trace which agent
    # made which decision without reading the full, potentially long, log.
    report += "\n## Conversation Transcript\n"
    transcript = s.get("transcript", [])
    if transcript:
        repo_root = _configured_repo_root(tool_context)
        full_path = _state_file_path(repo_root)
        try:
            transcript_location = str(full_path.relative_to(repo_root))
        except ValueError:
            transcript_location = str(full_path)
        report += (
            f"Full transcript ({len(transcript)} entries) persisted at "
            f"`{transcript_location}` (`transcript` key).\n\n"
        )
        report += "Most recent contribution per agent:\n"
        for entry in _summarize_transcript(transcript):
            agent_name = entry.get("agent_name", "unknown")
            content = entry.get("content", "")
            excerpt = content if len(content) <= 200 else content[:200].rstrip() + "..."
            report += f"- **{agent_name}**: {excerpt}\n"
    else:
        report += "No transcript available yet for this sprint.\n"

    s["sprint_report"] = report
    numbered_path = _next_sprint_report_path(tool_context)
    write_file(numbered_path, report, overwrite=True, tool_context=tool_context)
    latest_path = "specs/reports/SPRINT-REPORT-LATEST.md"
    write_file(latest_path, report, overwrite=True, tool_context=tool_context)

    return {"status": "ok", "report": report, "path": numbered_path, "latest_path": latest_path}

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